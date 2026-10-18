"""
Structured output / strict tool calling & SQL validation tests.

覆盖：
- make_strict_json_schema：additionalProperties=false、全字段 required、移除 default
- Tool.get_schema(strict=True) 与 ToolRegistry.get_schemas(strict=True)
- AgentConfig.strict_tools 透传到 LlmRequest
- OpenAI/Ollama 适配器的严格工具与 response_format 翻译
- RunSqlTool 的结构化 SQL 校验（空/多语句/超长/只读）
"""

from typing import Dict, List

import pandas as pd
import pytest
from pydantic import BaseModel, Field

from vanna.capabilities.sql_runner import RunSqlToolArgs, SqlRunner
from vanna.core import (
    Agent,
    AgentConfig,
    LlmRequest,
    LlmResponse,
    LlmService,
    LlmStreamChunk,
    Tool,
    ToolContext,
    ToolResult,
    ToolSchema,
    User,
    make_strict_json_schema,
)
from vanna.core.registry import ToolRegistry
from vanna.core.user.request_context import RequestContext
from vanna.core.user.resolver import UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.tools import RunSqlTool
from vanna.tools.sql_validation import split_statements, validate_sql


# --------------------------------------------------------------------------- #
# strict JSON schema
# --------------------------------------------------------------------------- #
def test_make_strict_json_schema_objects_and_defaults():
    schema = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "title": "Sql"},
            "limit": {"type": "integer", "default": 10, "title": "Limit"},
            "nested": {
                "type": "object",
                "properties": {"a": {"type": "string", "default": "x"}},
            },
        },
    }
    strict = make_strict_json_schema(schema)
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == {"sql", "limit", "nested"}
    assert "default" not in strict["properties"]["limit"]
    assert strict["properties"]["nested"]["additionalProperties"] is False
    assert set(strict["properties"]["nested"]["required"]) == {"a"}
    assert "default" not in strict["properties"]["nested"]["properties"]["a"]
    # 原始 schema 不被修改
    assert "default" in schema["properties"]["limit"]


class _Args(BaseModel):
    text: str = Field(description="text")
    limit: int = Field(default=10, description="limit")


class _EchoTool(Tool[_Args]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo"

    def get_args_schema(self):
        return _Args

    async def execute(self, context: ToolContext, args: _Args) -> ToolResult:
        return ToolResult(success=True, result_for_llm=f"{args.text}:{args.limit}")


def test_tool_get_schema_strict():
    tool = _EchoTool()
    normal = tool.get_schema()
    strict = tool.get_schema(strict=True)

    assert normal.strict is False
    assert "required" not in normal.parameters or "limit" not in normal.parameters.get(
        "required", []
    )

    assert strict.strict is True
    assert strict.parameters["additionalProperties"] is False
    assert set(strict.parameters["required"]) == {"text", "limit"}
    assert "default" not in strict.parameters["properties"]["limit"]


async def test_registry_get_schemas_strict():
    registry = ToolRegistry()
    registry.register_local_tool(_EchoTool(), access_groups=[])
    schemas = await registry.get_schemas(strict=True)
    assert schemas and schemas[0].strict is True
    assert schemas[0].parameters["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# Agent -> LlmRequest plumbing
# --------------------------------------------------------------------------- #
class _Resolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", group_memberships=["user"])


class _CapturingLlm(LlmService):
    def __init__(self) -> None:
        self.requests: List[LlmRequest] = []

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(content="ok", finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        self.requests.append(request)
        yield LlmStreamChunk(content="ok", finish_reason="stop")

    async def validate_tools(self, tools):
        return []


async def test_agent_propagates_strict_tools():
    llm = _CapturingLlm()
    registry = ToolRegistry()
    registry.register_local_tool(_EchoTool(), access_groups=[])
    agent = Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        config=AgentConfig(strict_tools=True),
    )

    async for _ in agent.send_message(RequestContext(cookies={}, headers={}), "hi"):
        pass

    assert llm.requests, "LLM should have been called"
    request = llm.requests[0]
    assert request.strict_tools is True
    assert request.tools and all(t.strict for t in request.tools)
    parameters = request.tools[0].parameters
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == set(parameters["properties"].keys())


async def test_agent_without_strict_tools_keeps_default():
    llm = _CapturingLlm()
    registry = ToolRegistry()
    registry.register_local_tool(_EchoTool(), access_groups=[])
    agent = Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
    )
    async for _ in agent.send_message(RequestContext(cookies={}, headers={}), "hi"):
        pass
    assert llm.requests[0].strict_tools is False
    assert llm.requests[0].response_format is None


# --------------------------------------------------------------------------- #
# Provider adapters
# --------------------------------------------------------------------------- #
def test_openai_payload_strict_tools_and_response_format():
    from vanna.integrations.openai.llm import OpenAILlmService

    service = OpenAILlmService.__new__(OpenAILlmService)
    service.model = "gpt-4o"

    request = LlmRequest(
        messages=[],
        user=User(id="u"),
        tools=[
            ToolSchema(
                name="run_sql",
                description="run",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                strict=True,
            )
        ],
        strict_tools=True,
        response_format={"type": "json_object"},
    )
    payload = service._build_payload(request)
    assert payload["tools"][0]["function"]["strict"] is True
    assert payload["response_format"] == {"type": "json_object"}


def test_ollama_response_format_translation():
    from vanna.integrations.ollama.llm import OllamaLlmService

    assert OllamaLlmService._translate_response_format({"type": "json_object"}) == "json"
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert (
        OllamaLlmService._translate_response_format(
            {"type": "json_schema", "json_schema": {"schema": schema}}
        )
        == schema
    )
    assert OllamaLlmService._translate_response_format({"type": "text"}) is None


# --------------------------------------------------------------------------- #
# SQL validation
# --------------------------------------------------------------------------- #
def test_validate_sql_ok():
    result = validate_sql("SELECT * FROM t")
    assert result.ok is True
    assert result.statement_type == "SELECT"


def test_validate_sql_empty():
    result = validate_sql("   ")
    assert result.ok is False
    assert result.error_type == "empty_sql"


def test_validate_sql_rejects_multiple_statements():
    result = validate_sql("SELECT 1; DROP TABLE t")
    assert result.ok is False
    assert result.error_type == "multiple_statements"


def test_validate_sql_trailing_semicolon_is_single_statement():
    assert len(split_statements("SELECT 1;")) == 1
    assert validate_sql("SELECT 1;").ok is True


def test_validate_sql_read_only():
    result = validate_sql("DELETE FROM t", read_only=True)
    assert result.ok is False
    assert result.error_type == "read_only_violation"


def test_validate_sql_max_length():
    result = validate_sql("SELECT 1234567890", max_length=5)
    assert result.ok is False
    assert result.error_type == "sql_too_long"


class _RecordingRunner(SqlRunner):
    def __init__(self) -> None:
        self.calls: List[str] = []

    async def run_sql(self, args: RunSqlToolArgs, context: ToolContext) -> pd.DataFrame:
        self.calls.append(args.sql)
        return pd.DataFrame({"x": [1]})


def _tool_context() -> ToolContext:
    return ToolContext(
        user=User(id="u"),
        conversation_id="c",
        request_id="r",
        agent_memory=DemoAgentMemory(max_items=10),
        metadata={},
    )


async def test_run_sql_tool_rejects_multiple_statements_before_execution():
    runner = _RecordingRunner()
    tool = RunSqlTool(sql_runner=runner, read_only=True)
    result = await tool.execute(
        _tool_context(), RunSqlToolArgs(sql="SELECT 1; DROP TABLE t")
    )
    assert result.success is False
    assert result.metadata["error_type"] == "multiple_statements"
    assert runner.calls == []


async def test_run_sql_tool_allows_single_select():
    runner = _RecordingRunner()
    tool = RunSqlTool(sql_runner=runner, read_only=True)
    result = await tool.execute(_tool_context(), RunSqlToolArgs(sql="SELECT 1"))
    assert result.success is True
    assert runner.calls == ["SELECT 1"]
