# All imports at the top
from vanna import Agent
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.tools import RunSqlTool, VisualizeDataTool
from vanna.tools.agent_memory import SaveQuestionToolArgsTool, SearchSavedCorrectToolUsesTool, SaveTextMemoryTool
from vanna.integrations.ollama import OllamaLlmService
from vanna.integrations.clickhouse import ClickHouseRunner
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.servers.base import ChatHandler
from vanna.servers.fastapi.routes import register_chat_routes
from fastapi import FastAPI

# Configure your LLM
llm = OllamaLlmService(
    model="qwen2.5:7b",
    host="http://192.168.0.228:11434"
)

# Configure your database
db_tool = RunSqlTool(
    sql_runner=ClickHouseRunner(
        host="192.168.0.193",
        database="default",
        user="default",
        password="hh123456",
        port=8123
    )
)

# Configure your agent memory
agent_memory = DemoAgentMemory(max_items=1000)

# Configure user authentication
class SimpleUserResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        user_email = request_context.get_cookie('vanna_email') or 'guest@example.com'
        group = 'admin' if user_email == 'admin@example.com' else 'user'
        return User(id=user_email, email=user_email, group_memberships=[group])

user_resolver = SimpleUserResolver()

# Create your agent
tools = ToolRegistry()
tools.register_local_tool(db_tool, access_groups=['admin', 'user'])
tools.register_local_tool(SaveQuestionToolArgsTool(), access_groups=['admin'])
tools.register_local_tool(SearchSavedCorrectToolUsesTool(), access_groups=['admin', 'user'])
tools.register_local_tool(SaveTextMemoryTool(), access_groups=['admin', 'user'])
tools.register_local_tool(VisualizeDataTool(), access_groups=['admin', 'user'])

agent = Agent(
    llm_service=llm,
    tool_registry=tools,
    user_resolver=user_resolver,
    agent_memory=agent_memory
)


# =============================================================================
# 前端说明：不再在示例里内联 HTML。
#
# 默认首页由 register_chat_routes 统一注册，使用 servers/base/templates.py 里的
# 极简对话框（含「停止」按钮，支持 token 级中断）。所有 *.py 示例共享同一默认前端。
# 如需自定义，可在调用 register_chat_routes 之前先注册自己的 "/" 路由覆盖默认首页
# （Starlette 按注册顺序匹配，先注册的 "/" 会命中，后面的默认首页即成为死路由）。
# =============================================================================
app = FastAPI()

chat_handler = ChatHandler(agent)
register_chat_routes(app, chat_handler, {})


if __name__ == "__main__":
    import uvicorn

    # Access at http://localhost:8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
