"""
Evaluation runner with parallel execution support.

This module provides the EvaluationRunner class that executes test cases
against agents with configurable parallelism for efficient evaluation,
especially when comparing multiple LLMs or model versions.

本模块提供评测执行器 EvaluationRunner：用可配置并发对智能体跑测试用例，支持单个
评测、多变体对比与流式对比，尤其适用于「不同 LLM/模型版本」的高效对比评测。
"""

import asyncio
from contextvars import ContextVar
from typing import Any, List, Dict, Optional, AsyncGenerator, TYPE_CHECKING
from datetime import datetime

from .base import (
    TestCase,
    AgentResult,
    TestCaseResult,
    AgentVariant,
    Evaluator,
)
from vanna.core import UiComponent
from vanna.core.llm import LlmRequest, LlmResponse
from vanna.core.middleware import LlmMiddleware
from vanna.core.user.request_context import RequestContext
from vanna.core.observability import ObservabilityProvider

if TYPE_CHECKING:
    from vanna import Agent
    from .report import EvaluationReport, ComparisonReport


# 评测轨迹采集容器：用 ContextVar 绑定到「当前测试用例所在的 task」，
# 这样并发评测多个用例时，各自的 LLM 请求/工具调用不会串到一起。
_EVAL_CAPTURE: ContextVar[Optional[Dict[str, List[Dict[str, Any]]]]] = ContextVar(
    "vanna_eval_capture", default=None
)


class _TrajectoryCaptureMiddleware(LlmMiddleware):
    """Capture LLM requests and requested tool calls for evaluation.

    - before_llm_request: 记录每次发往 LLM 的请求（消息、工具清单、系统提示词）
    - after_llm_response: 记录模型请求调用的工具（名称、参数、call id）

    评测期间由 EvaluationRunner 临时挂载到 Agent 上，结束后移除。
    """

    async def before_llm_request(self, request: LlmRequest) -> LlmRequest:
        capture = _EVAL_CAPTURE.get()
        if capture is not None:
            capture["llm_requests"].append(
                {
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in request.messages
                    ],
                    "tools": [tool.name for tool in (request.tools or [])],
                    "system_prompt": request.system_prompt,
                }
            )
        return request

    async def after_llm_response(
        self, request: LlmRequest, response: LlmResponse
    ) -> LlmResponse:
        capture = _EVAL_CAPTURE.get()
        if capture is not None:
            for tool_call in response.tool_calls or []:
                capture["tool_calls"].append(
                    {
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "tool_call_id": tool_call.id,
                    }
                )
        return response


class EvaluationRunner:
    """Run evaluations with parallel execution support.

    The primary use case is comparing multiple agent variants (e.g., different LLMs)
    on the same set of test cases. The runner executes test cases in parallel with
    configurable concurrency to handle I/O-bound LLM operations efficiently.

    Example:
        >>> runner = EvaluationRunner(
        ...     evaluators=[TrajectoryEvaluator(), OutputEvaluator()],
        ...     max_concurrency=20
        ... )
        >>> comparison = await runner.compare_agents(
        ...     agent_variants=[claude_variant, gpt_variant],
        ...     test_cases=dataset.test_cases
        ... )

    评测执行器：内部用 asyncio.Semaphore 限流并发；compare_agents 是 LLM 对比的主
    入口（并行跑所有变体），compare_agents_streaming 用于实时观察进度的长评测。
    """

    def __init__(
        self,
        evaluators: List[Evaluator],
        max_concurrency: int = 10,
        observability_provider: Optional[ObservabilityProvider] = None,
    ):
        """Initialize the evaluation runner.

        Args:
            evaluators: List of evaluators to apply to each test case
            max_concurrency: Maximum number of concurrent test case executions
            observability_provider: Optional observability for tracking eval runs
        """
        self.evaluators = evaluators
        self.max_concurrency = max_concurrency
        self.observability = observability_provider
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_evaluation(
        self,
        agent: "Agent",
        test_cases: List[TestCase],
    ) -> "EvaluationReport":
        """Run evaluation on a single agent.

        Args:
            agent: The agent to evaluate
            test_cases: List of test cases to run

        Returns:
            EvaluationReport with results for all test cases
        """
        from .report import EvaluationReport

        results = await self._run_test_cases_parallel(agent, test_cases)
        return EvaluationReport(
            agent_name="agent",
            results=results,
            evaluators=self.evaluators,
            timestamp=datetime.now(),
        )

    async def compare_agents(
        self,
        agent_variants: List[AgentVariant],
        test_cases: List[TestCase],
    ) -> "ComparisonReport":
        """Compare multiple agent variants on same test cases.

        This is the PRIMARY use case for LLM comparison. Runs all variants
        in parallel for maximum efficiency with I/O-bound LLM calls.

        Args:
            agent_variants: List of agent variants to compare
            test_cases: Test cases to run on each variant

        Returns:
            ComparisonReport with results for all variants
        """
        from .report import ComparisonReport

        # Create span for overall comparison
        if self.observability:
            span = await self.observability.create_span(
                "agent_comparison",
                attributes={
                    "num_variants": len(agent_variants),
                    "num_test_cases": len(test_cases),
                },
            )

        # Run all variants in parallel
        tasks = [
            self._run_agent_variant(variant, test_cases) for variant in agent_variants
        ]

        variant_reports = await asyncio.gather(*tasks)

        if self.observability:
            await self.observability.end_span(span)

        return ComparisonReport(
            variants=agent_variants,
            reports=dict(zip([v.name for v in agent_variants], variant_reports)),
            test_cases=test_cases,
            timestamp=datetime.now(),
        )

    async def compare_agents_streaming(
        self,
        agent_variants: List[AgentVariant],
        test_cases: List[TestCase],
    ) -> AsyncGenerator[tuple[str, TestCaseResult, int, int], None]:
        """Stream comparison results as they complete.

        Useful for long-running evaluations where you want to see
        progress updates in real-time (e.g., for UI display).

        Args:
            agent_variants: Agent variants to compare
            test_cases: Test cases to run

        Yields:
            Tuples of (variant_name, result, completed_count, total_count)
        """
        queue: asyncio.Queue[tuple[str, TestCaseResult]] = asyncio.Queue()

        async def worker(variant: AgentVariant) -> None:
            """Worker that runs test cases for one variant."""
            results = await self._run_test_cases_parallel(variant.agent, test_cases)
            for result in results:
                await queue.put((variant.name, result))

        # Start all workers
        workers = [asyncio.create_task(worker(v)) for v in agent_variants]

        # Yield results as they arrive
        completed = 0
        total = len(agent_variants) * len(test_cases)

        while completed < total:
            variant_name, result = await queue.get()
            completed += 1
            yield variant_name, result, completed, total

        # Wait for all workers to complete
        await asyncio.gather(*workers)

    async def _run_agent_variant(
        self,
        variant: AgentVariant,
        test_cases: List[TestCase],
    ) -> "EvaluationReport":
        """Run a single agent variant on all test cases.

        Args:
            variant: The agent variant to evaluate
            test_cases: Test cases to run

        Returns:
            EvaluationReport for this variant
        """
        from .report import EvaluationReport

        if self.observability:
            span = await self.observability.create_span(
                f"variant_{variant.name}",
                attributes={
                    "variant": variant.name,
                    "num_test_cases": len(test_cases),
                    **variant.metadata,
                },
            )

        results = await self._run_test_cases_parallel(variant.agent, test_cases)

        if self.observability:
            await self.observability.end_span(span)

        return EvaluationReport(
            agent_name=variant.name,
            results=results,
            evaluators=self.evaluators,
            metadata=variant.metadata,
            timestamp=datetime.now(),
        )

    async def _run_test_cases_parallel(
        self,
        agent: "Agent",
        test_cases: List[TestCase],
    ) -> List[TestCaseResult]:
        """Run test cases in parallel with concurrency limit.

        Args:
            agent: The agent to run test cases on
            test_cases: Test cases to execute

        Returns:
            List of TestCaseResult, one per test case
        """
        # 评测期间临时挂载轨迹采集中间件；结束后移除，避免污染 Agent 的常规运行。
        capture_middleware: Optional[_TrajectoryCaptureMiddleware] = None
        already_present = any(
            isinstance(middleware, _TrajectoryCaptureMiddleware)
            for middleware in agent.llm_middlewares
        )
        if not already_present:
            capture_middleware = _TrajectoryCaptureMiddleware()
            agent.llm_middlewares.append(capture_middleware)

        try:
            tasks = [
                self._run_single_test_case(agent, test_case)
                for test_case in test_cases
            ]
            return await asyncio.gather(*tasks)
        finally:
            if capture_middleware is not None:
                agent.llm_middlewares = [
                    middleware
                    for middleware in agent.llm_middlewares
                    if middleware is not capture_middleware
                ]

    async def _run_single_test_case(
        self,
        agent: "Agent",
        test_case: TestCase,
    ) -> TestCaseResult:
        """Run a single test case with semaphore to limit concurrency.

        Args:
            agent: The agent to execute
            test_case: The test case to run

        Returns:
            TestCaseResult with agent execution and evaluations
        """
        async with self._semaphore:
            # Execute agent
            start_time = asyncio.get_event_loop().time()
            agent_result = await self._execute_agent(agent, test_case)
            execution_time = asyncio.get_event_loop().time() - start_time

            # Run evaluators
            eval_results = []
            for evaluator in self.evaluators:
                eval_result = await evaluator.evaluate(test_case, agent_result)
                eval_results.append(eval_result)

            return TestCaseResult(
                test_case=test_case,
                agent_result=agent_result,
                evaluations=eval_results,
                execution_time_ms=execution_time * 1000,
            )

    async def _execute_agent(
        self,
        agent: "Agent",
        test_case: TestCase,
    ) -> AgentResult:
        """Execute agent and capture full trajectory.

        Args:
            agent: The agent to execute
            test_case: The test case to run

        Returns:
            AgentResult with all captured data
        """
        components: List[UiComponent] = []
        capture: Dict[str, List[Dict[str, Any]]] = {
            "tool_calls": [],
            "llm_requests": [],
        }
        error: Optional[str] = None

        # 把当前用例的采集容器绑定到 task 上下文；中间件在 LLM 调用前后写入。
        # 这样并发跑多个用例时，各 task 的 ContextVar 天然隔离，不会串数据。
        token = _EVAL_CAPTURE.set(capture)
        try:
            # Create request context with user info from test case
            # This allows the agent's UserResolver to resolve the correct user
            request_context = RequestContext(
                cookies={"user_id": test_case.user.id},
                headers={},
                metadata={"test_case_user": test_case.user},
            )

            async for component in agent.send_message(
                request_context=request_context,
                message=test_case.message,
                conversation_id=test_case.conversation_id,
            ):
                components.append(component)

        except Exception as e:
            error = str(e)
        finally:
            _EVAL_CAPTURE.reset(token)

        return AgentResult(
            test_case_id=test_case.id,
            components=components,
            tool_calls=capture["tool_calls"],
            llm_requests=capture["llm_requests"],
            error=error,
        )
