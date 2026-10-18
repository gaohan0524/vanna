# 本地手动测试：验证会话与智能体记忆是否持久化到 Redis。
#
# 说明：
# - 参照 test.py 的配置（Ollama + ClickHouse + 简易身份解析），但把存储后端换成 Redis。
# - 启动后浏览器访问 http://localhost:8000 聊天；然后访问 http://localhost:8000/debug/redis
#   查看 Redis 中实际写入的会话 / 消息 / 记忆。
# - 重启本进程后再次访问 /debug/redis，若数据仍在，即证明持久化生效。
# - 本文件含明文口令，已加入 .gitignore，仅本地使用。

from urllib.parse import urlparse, urlunparse

import redis.asyncio as aioredis
from fastapi import FastAPI

from vanna import Agent
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.tools import RunSqlTool, VisualizeDataTool
from vanna.tools.agent_memory import (
    SaveQuestionToolArgsTool,
    SearchSavedCorrectToolUsesTool,
    SaveTextMemoryTool,
)
from vanna.integrations.ollama import OllamaLlmService
from vanna.integrations.clickhouse import ClickHouseRunner
from vanna.integrations.redis import RedisAgentMemory, RedisConversationStore
from vanna.servers.base import ChatHandler
from vanna.servers.fastapi.routes import register_chat_routes

# =============================================================================
# Redis 连接（按你的实例填写）
# 格式：redis://[:password@]host:port/db
# =============================================================================
REDIS_URL = "redis://:glad123456@192.168.0.193:6379/0"
REDIS_PREFIX = "vanna"  # 所有 key 以此前缀开头，便于用 /debug/redis 查看与清理


# Configure your LLM
llm = OllamaLlmService(model="qwen2.5:7b", host="http://192.168.0.228:11434")

# Configure your database
db_tool = RunSqlTool(
    sql_runner=ClickHouseRunner(
        host="192.168.0.193",
        database="default",
        user="default",
        password="hh123456",
        port=8123,
    )
)


# Configure user authentication
class SimpleUserResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        user_email = request_context.get_cookie("vanna_email") or "guest@example.com"
        group = "admin" if user_email == "admin@example.com" else "user"
        return User(id=user_email, email=user_email, group_memberships=[group])


# =============================================================================
# 存储后端：Redis（会话 + 记忆都持久化）
# 默认按 user_id 隔离；如需同租户共享，加 share_within_tenant=True + tenant_resolver
# =============================================================================
conversation_store = RedisConversationStore(REDIS_URL, prefix=REDIS_PREFIX)
agent_memory = RedisAgentMemory(REDIS_URL, prefix=REDIS_PREFIX)

# Configure your agent
tools = ToolRegistry()
tools.register_local_tool(db_tool, access_groups=["admin", "user"])
tools.register_local_tool(SaveQuestionToolArgsTool(), access_groups=["admin"])
tools.register_local_tool(
    SearchSavedCorrectToolUsesTool(), access_groups=["admin", "user"]
)
tools.register_local_tool(SaveTextMemoryTool(), access_groups=["admin", "user"])
tools.register_local_tool(VisualizeDataTool(), access_groups=["admin", "user"])

agent = Agent(
    llm_service=llm,
    tool_registry=tools,
    user_resolver=SimpleUserResolver(),
    agent_memory=agent_memory,
    conversation_store=conversation_store,
)


# =============================================================================
# 调试用 Redis 客户端 + 端点
# =============================================================================
redis_client = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)

# FastAPI 应用需先创建，下面才能挂载调试路由与聊天路由
app = FastAPI()


def _masked_url(url: str) -> str:
    """隐藏连接串中的口令，避免调试页泄露。"""
    parsed = urlparse(url)
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
        return urlunparse(parsed._replace(netloc=netloc))
    return url


@app.get("/debug/redis")
async def debug_redis() -> dict:
    """查看 Redis 中持久化的会话 / 消息 / 记忆（只读）。"""
    keys = sorted(await redis_client.keys(f"{REDIS_PREFIX}:*"))

    conversations = []
    tool_memories = []
    text_memories = []
    indexes = {}

    for key in keys:
        if key.endswith(":messages"):
            continue
        if key.startswith(f"{REDIS_PREFIX}:conv:"):
            data = await redis_client.hgetall(key)
            message_count = await redis_client.llen(f"{key}:messages")
            conversations.append(
                {
                    "key": key,
                    "id": data.get("id"),
                    "user_id": data.get("user_id"),
                    "tenant_id": data.get("tenant_id"),
                    "updated_at": data.get("updated_at"),
                    "message_count": message_count,
                }
            )
        elif key.startswith(f"{REDIS_PREFIX}:tool_mem:"):
            tool_memories.append({"key": key, **(await redis_client.hgetall(key))})
        elif key.startswith(f"{REDIS_PREFIX}:text_mem:"):
            text_memories.append({"key": key, **(await redis_client.hgetall(key))})
        elif key.startswith(f"{REDIS_PREFIX}:user:") or key.startswith(
            f"{REDIS_PREFIX}:tenant:"
        ):
            indexes[key] = await redis_client.zcard(key)

    return {
        "redis_url": _masked_url(REDIS_URL),
        "prefix": REDIS_PREFIX,
        "dbsize": await redis_client.dbsize(),
        "total_keys": len(keys),
        "keys": keys,
        "counts": {
            "conversations": len(conversations),
            "tool_memories": len(tool_memories),
            "text_memories": len(text_memories),
        },
        "conversations": conversations,
        "tool_memories": tool_memories,
        "text_memories": text_memories,
        "indexes": indexes,
    }


@app.get("/debug/redis/messages/{conversation_id}")
async def debug_redis_messages(conversation_id: str) -> dict:
    """查看某个会话的完整消息列表（原始 JSON）。"""
    key = f"{REDIS_PREFIX}:conv:{conversation_id}:messages"
    raw = await redis_client.lrange(key, 0, -1)
    return {"key": key, "count": len(raw), "messages": raw}


@app.post("/debug/redis/clear")
async def clear_redis(confirm: bool = False) -> dict:
    """清空本前缀下的所有 key（需显式 confirm=true）。"""
    if not confirm:
        return {"error": "请使用 POST /debug/redis/clear?confirm=true 以确认清空"}
    keys = await redis_client.keys(f"{REDIS_PREFIX}:*")
    deleted = 0
    for i in range(0, len(keys), 500):
        deleted += await redis_client.delete(*keys[i : i + 500])
    return {"deleted": deleted}


# =============================================================================
# 注册聊天路由（默认首页由 register_chat_routes 提供）
# =============================================================================
@app.on_event("startup")
async def _startup() -> None:
    """启动时探测 Redis，并打印已有 key 数量，便于确认是否连对实例。"""
    pong = await redis_client.ping()
    keys = await redis_client.keys(f"{REDIS_PREFIX}:*")
    print(f"[redis] connected={pong} url={_masked_url(REDIS_URL)}")
    print(f"[redis] existing '{REDIS_PREFIX}:*' keys: {len(keys)}")


chat_handler = ChatHandler(agent)
register_chat_routes(app, chat_handler, {})


if __name__ == "__main__":
    import uvicorn

    # 访问 http://localhost:8000 聊天；访问 http://localhost:8000/debug/redis 查看 Redis
    uvicorn.run(app, host="0.0.0.0", port=8000)
