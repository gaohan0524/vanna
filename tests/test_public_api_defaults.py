"""
公开 API 与安全默认值测试（#1/#3/#4）。

覆盖：
- RunSqlTool 默认 read_only=True / max_rows=1000
- ToolRegistry.register 兼容入口
- create_basic_agent 工厂
- 顶层 __all__ 名字可导入（含懒加载的 VannaFastAPIServer）与版本号一致
"""

from pydantic import BaseModel

import vanna
from vanna import Agent
from vanna.agents import create_basic_agent
from vanna.core.registry import ToolRegistry
from vanna.core.tool import Tool, ToolContext, ToolResult
from vanna.integrations.mock import MockLlmService
from vanna.integrations.sqlite import SqliteRunner
from vanna.tools import RunSqlTool


class _Args(BaseModel):
    pass


class _NoopTool(Tool):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "noop"

    def get_args_schema(self):
        return _Args

    async def execute(self, context: ToolContext, args: _Args) -> ToolResult:
        return ToolResult(success=True, result_for_llm="ok")


def test_run_sql_secure_defaults():
    tool = RunSqlTool(sql_runner=SqliteRunner(":memory:"))
    assert tool.read_only is True
    assert tool.max_rows == 1000


async def test_registry_register_alias():
    registry = ToolRegistry()
    registry.register(_NoopTool())
    assert "noop" in await registry.list_tools()


def test_create_basic_agent():
    agent = create_basic_agent(MockLlmService())
    assert isinstance(agent, Agent)
    assert agent.tool_registry is not None
    assert agent.user_resolver is not None
    assert agent.agent_memory is not None


def test_public_api_names_and_version():
    # 版本号从包元数据读取，与 pyproject.toml 一致
    assert vanna.__version__ == "2.0.2"

    # 顶层直接导出的服务器组件
    from vanna import (  # noqa: F401
        ChatHandler,
        ChatRequest,
        ChatStreamChunk,
        ExampleAgentLoader,
    )

    # 懒加载的 FastAPI server 类
    from vanna import VannaFastAPIServer  # noqa: F401

    assert VannaFastAPIServer is not None
