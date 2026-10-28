"""Agent assembly: the single place where the tested agent is built.

评测与线上共用同一套组装逻辑，避免“评测的 agent 和上线的 agent 不是同一个东西”。
"""

from __future__ import annotations

from typing import Optional, Union

from vanna import Agent, AgentConfig
from vanna.core.registry import ToolRegistry
from vanna.core.user import RequestContext, User, UserResolver
from vanna.integrations.clickhouse import ClickHouseRunner
from vanna.integrations.local.agent_memory import DemoAgentMemory
from .tools.run_sql import AnalyticsRunSqlTool
from vanna.tools.agent_memory import (
    SaveQuestionToolArgsTool,
    SearchSavedCorrectToolUsesTool,
)

from .config import Settings
from .context.glossary import load_glossary, render_glossary
from .context.render import render_schema
from .context.schema_catalog import load_catalog
from .prompts.builder import AnalyticsPromptBuilder
from .tools.describe_table import DescribeTableTool
from .tools.descriptions import RUN_SQL_DESCRIPTION
from .tools.explain_query import ExplainQueryTool
from .tools.metric_definition import GetMetricDefinitionTool
from .tools.sample_rows import SampleRowsTool
from .tools.search_columns import SearchColumnsTool
from .tools.visualize import AnalyticsVisualizeTool


class EvalUserResolver(UserResolver):
    """Resolve the user for evaluation runs.

    `EvaluationRunner` 会把 `TestCase.user` 放进
    `RequestContext.metadata["test_case_user"]`，这里直接取用；
    线上运行时再换成读取 cookie / JWT 的实现。
    """

    async def resolve_user(self, request_context: RequestContext) -> User:
        injected = request_context.metadata.get("test_case_user")
        if isinstance(injected, User):
            return injected

        user_id = request_context.get_cookie("user_id", "eval_user") or "eval_user"
        return User(
            id=user_id,
            username=user_id,
            email=f"{user_id}@example.com",
            group_memberships=["analyst"],
        )


def build_llm_service(settings: Settings):
    """Instantiate the configured LLM service.

    只做惰性导入：没用到的供应商不会要求安装对应 extras。
    """
    provider = settings.llm_provider

    if provider == "ollama":
        from vanna.integrations.ollama import OllamaLlmService

        # 注意：OllamaLlmService 使用的是自身构造参数里的 temperature / num_ctx，
        # 不会读 LlmRequest.temperature（见 integrations/ollama/llm.py）。
        # 因此必须在这里显式传 temperature=0，否则 AgentConfig.temperature 形同虚设。
        return OllamaLlmService(
            model=settings.llm_model,
            host=settings.ollama_host,
            temperature=0.0,
            num_ctx=settings.llm_num_ctx,
            timeout=float(settings.llm_timeout_s),
            # 7B 在 CPU 上约 10 tok/s，必须给生成设上限，否则单次回答可能超时
            num_predict=settings.llm_max_predict,
        )

    if provider == "deepseek":
        from vanna.integrations.deepseek import DeepSeekLlmService

        return DeepSeekLlmService(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key or None,
            base_url=settings.deepseek_base_url,
            timeout=float(settings.llm_timeout_s),
        )

    if provider == "openai":
        from vanna.integrations.openai import OpenAILlmService

        return OpenAILlmService(model=settings.llm_model)

    if provider == "anthropic":
        from vanna.integrations.anthropic import AnthropicLlmService

        return AnthropicLlmService(model=settings.llm_model)

    raise ValueError(
        f"Unsupported LLM provider: {provider!r} (expected ollama/openai/anthropic)"
    )


def build_tool_registry(
    settings: Settings,
    sql_runner: Optional[ClickHouseRunner] = None,
    *,
    enable_memory_tools: bool = False,
    catalog: Optional[dict] = None,
    glossary: Optional[dict] = None,
) -> ToolRegistry:
    """Register the tools available to the agent.

    Stage 0 只保留最小集合（run_sql + visualize_data）：
    - `DefaultSystemPromptBuilder` 只要发现记忆工具，就会强制“先检索再执行”，
      在 CPU 推理下等于每次多一轮 LLM 调用；
    - 记忆工具留到 Stage 3（检索层）再打开。

    保存类工具仅 admin 可用，避免评测期间污染记忆。
    """
    runner = sql_runner or ClickHouseRunner(
        host=settings.ch_host,
        port=settings.ch_port,
        database=settings.ch_database,
        user=settings.ch_user,
        password=settings.ch_password,
        # 服务端超时：BLOCKING=True 时 asyncio.wait_for 打不断线程
        query_settings={"max_execution_time": 30},
    )

    catalog = catalog or load_catalog(settings.schema_catalog_path)
    glossary = glossary or load_glossary(settings.glossary_path)

    registry = ToolRegistry()
    registry.register_local_tool(
        AnalyticsRunSqlTool(
            sql_runner=runner,
            custom_tool_description=RUN_SQL_DESCRIPTION,
            auto_limit=1000,
        ),
        access_groups=["admin", "analyst"],
    )
    registry.register_local_tool(
        AnalyticsVisualizeTool(), access_groups=["admin", "analyst"]
    )
    registry.register_local_tool(
        SearchColumnsTool(catalog), access_groups=["admin", "analyst"]
    )
    registry.register_local_tool(
        DescribeTableTool(catalog), access_groups=["admin", "analyst"]
    )
    registry.register_local_tool(
        SampleRowsTool(catalog, runner), access_groups=["admin", "analyst"]
    )
    registry.register_local_tool(
        ExplainQueryTool(runner), access_groups=["admin", "analyst"]
    )
    registry.register_local_tool(
        GetMetricDefinitionTool(glossary), access_groups=["admin", "analyst"]
    )
    if enable_memory_tools:
        registry.register_local_tool(
            SearchSavedCorrectToolUsesTool(), access_groups=["admin", "analyst"]
        )
        registry.register_local_tool(
            SaveQuestionToolArgsTool(), access_groups=["admin"]
        )
    return registry


def build_memory(settings: Settings):
    """Build the configured AgentMemory backend.

    Default remains DemoAgentMemory so existing tests/evaluation do not
    download an embedding model. Set ``MEMORY_BACKEND=chroma_local`` for
    Stage 3 local retrieval.
    """
    backend = (settings.memory_backend or "demo").strip().lower()
    if backend in {"", "demo", "none"}:
        return DemoAgentMemory(max_items=10_000)

    if backend == "chroma_local":
        from analytics_agent.memory.backends.chroma_local import ChromaLocalMemory
        from analytics_agent.memory.embedding import ChineseEmbeddingFunction

        embedding = ChineseEmbeddingFunction(settings.memory_embedding_model)
        return ChromaLocalMemory(
            persist_directory=str(settings.memory_path),
            collection_name=settings.memory_collection,
            embedding_function=embedding,
        )

    raise ValueError(
        f"Unsupported MEMORY_BACKEND={settings.memory_backend!r}; "
        "expected demo or chroma_local"
    )


def build_prompt_builder(settings: Settings) -> AnalyticsPromptBuilder:
    """Load Stage 1 assets and assemble the prompt builder.

    缺 catalog / glossary 时直接抛错（不静默降级），错误信息包含刷新命令。
    """
    catalog = load_catalog(settings.schema_catalog_path)
    glossary = load_glossary(settings.glossary_path)
    return AnalyticsPromptBuilder(
        schema_text=render_schema(catalog),
        glossary_text=render_glossary(glossary),
    )


def build_agent(
    settings: Optional[Settings] = None,
    *,
    llm_service=None,
    agent_memory=None,
    prompt_builder: Optional[AnalyticsPromptBuilder] = None,
    stream_responses: bool = False,
    enable_memory_tools: bool = False,
) -> Agent:
    """Build the tested agent.

    Args:
        settings: 运行时配置；缺省从环境变量读取。
        llm_service: 可注入 mock/自定义 LLM（单元测试用）。
        agent_memory: 可注入记忆后端；Stage 3 会换成向量库实现。
        prompt_builder: 可注入自定义提示词构建器（测试用）。
        stream_responses: 评测时建议 False（少一层流式聚合）。
        enable_memory_tools: 是否注册记忆检索/保存工具（Stage 3 再打开）。
    """
    s = settings or Settings.from_env()
    builder = prompt_builder or build_prompt_builder(s)

    return Agent(
        llm_service=llm_service or build_llm_service(s),
        tool_registry=build_tool_registry(s, enable_memory_tools=enable_memory_tools),
        user_resolver=EvalUserResolver(),
        agent_memory=agent_memory or build_memory(s),
        system_prompt_builder=builder,
        config=AgentConfig(
            temperature=0.0,
            max_tool_iterations=s.agent_max_iterations,
            stream_responses=stream_responses,
        ),
    )
