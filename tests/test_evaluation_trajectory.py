"""
Evaluation trajectory capture tests.

验证 EvaluationRunner 能在评测期间采集 LLM 请求与工具调用轨迹，并且并发多个用例时
各用例的数据互相隔离（基于 ContextVar + 临时挂载的 LlmMiddleware）。
"""

import pytest
from pydantic import BaseModel

from vanna import Agent, AgentConfig
from vanna.core.evaluation import (
    EvaluationRunner,
    ExpectedOutcome,
    TestCase as EvalTestCase,
    TrajectoryEvaluator,
)
from vanna.core.llm import LlmRequest, LlmResponse, LlmService, LlmStreamChunk
from vanna.core.registry import ToolRegistry
from vanna.core.tool import Tool, ToolCall, ToolContext, ToolResult
from vanna.core.user import RequestContext, User, UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _Resolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", group_memberships=["user"])


class _Args(BaseModel):
    text: str = ""


class _EchoTool(Tool[_Args]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo"

    def get_args_schema(self):
        return _Args

    async def execute(self, context: ToolContext, args: _Args) -> ToolResult:
        return ToolResult(success=True, result_for_llm=f"echo: {args.text}")


class _EvalLlm(LlmService):
    """首次返回 tool_call；看到 tool 结果后返回最终答案（按请求内容判断，并发安全）。"""

    def __init__(self) -> None:
        self.calls: list[LlmRequest] = []

    @staticmethod
    def _has_tool_result(request: LlmRequest) -> bool:
        return any(message.role == "tool" for message in request.messages)

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        if not self._has_tool_result(request):
            return LlmResponse(
                tool_calls=[
                    ToolCall(id="t1", name="echo", arguments={"text": "hi"})
                ],
                finish_reason="tool_calls",
            )
        return LlmResponse(content="final answer", finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        self.calls.append(request)
        if not self._has_tool_result(request):
            yield LlmStreamChunk(
                tool_calls=[
                    ToolCall(id="t1", name="echo", arguments={"text": "hi"})
                ],
                finish_reason="tool_calls",
            )
        else:
            yield LlmStreamChunk(content="final answer", finish_reason="stop")

    async def validate_tools(self, tools):
        return []


def _make_agent(llm: LlmService) -> Agent:
    registry = ToolRegistry()
    registry.register_local_tool(_EchoTool(), access_groups=[])
    return Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        config=AgentConfig(stream_responses=False),
    )


def _test_case(case_id: str) -> EvalTestCase:
    return EvalTestCase(
        id=case_id,
        user=User(id="u"),
        message="hi",
        expected_outcome=ExpectedOutcome(tools_called=["echo"]),
    )


async def test_evaluation_captures_llm_requests_and_tool_calls():
    llm = _EvalLlm()
    agent = _make_agent(llm)
    runner = EvaluationRunner(evaluators=[TrajectoryEvaluator()])

    report = await runner.run_evaluation(agent, [_test_case("tc1")])
    result = report.results[0]

    # 工具调用轨迹
    assert [call["tool_name"] for call in result.agent_result.tool_calls] == ["echo"]
    assert result.agent_result.tool_calls[0]["arguments"] == {"text": "hi"}

    # LLM 请求轨迹（一次 tool_call 请求 + 一次最终答案请求）
    assert len(result.agent_result.llm_requests) == 2
    assert result.agent_result.llm_requests[0]["tools"] == ["echo"]

    # TrajectoryEvaluator 现在能真正基于轨迹判定
    assert result.evaluations[0].passed is True

    # 评测结束后临时中间件已移除
    from vanna.core.evaluation.runner import _TrajectoryCaptureMiddleware

    assert not any(
        isinstance(middleware, _TrajectoryCaptureMiddleware)
        for middleware in agent.llm_middlewares
    )


async def test_evaluation_capture_is_isolated_across_concurrent_cases():
    llm = _EvalLlm()
    agent = _make_agent(llm)
    runner = EvaluationRunner(evaluators=[TrajectoryEvaluator()], max_concurrency=5)

    cases = [_test_case(f"tc{i}") for i in range(4)]
    report = await runner.run_evaluation(agent, cases)

    assert len(report.results) == 4
    for result in report.results:
        assert [call["tool_name"] for call in result.agent_result.tool_calls] == [
            "echo"
        ]
        assert len(result.agent_result.llm_requests) == 2
        assert result.evaluations[0].passed is True
