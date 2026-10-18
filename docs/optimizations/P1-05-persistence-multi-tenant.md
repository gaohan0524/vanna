# P1-05 会话与记忆持久化 + 多租户隔离

- **对应问题**：#5 会话与记忆默认内存态，重启即丢
- **补充问题**：`DemoAgentMemory` 所有用户共享一个列表，存在跨用户数据泄露风险。
- **提交**：`c13633d feat: 持久化存储后端与配置化（SQLite/Postgres/MySQL/Redis + 多租户隔离）`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前，框架的两个存储扩展点默认都是内存态：

| 扩展点 | 默认实现 | 问题 |
|---|---|---|
| `conversation_store` | `MemoryConversationStore` | 重启丢失全部会话，无法支持多实例部署 |
| `agent_memory` | `DemoAgentMemory` | 重启丢失；且 `save_*` / `search_*` 都不按用户过滤，A 用户能检索到 B 用户的记忆 |

虽然接口本身支持替换（`ConversationStore` / `AgentMemory` 抽象基类），但**没有内置的持久化实现**，也没有统一的「选哪个后端」的配置入口，使用者必须自己写一套。

而主流框架（LangChain、LlamaIndex、Vercel AI SDK 等）至少会提供：

1. 明确的持久化默认值或开箱可用的持久化后端；
2. 明确的多租户/多用户隔离模型（按 `user_id` / `tenant_id` 强制过滤）。

---

## 2. 主流方案参考

| 维度 | 主流做法 | 本项目落地 |
|---|---|---|
| 本地/开发持久化 | SQLite、文件 | `SQLiteConversationStore` / `SQLiteAgentMemory` |
| 生产持久化 | PostgreSQL / MySQL | `Postgres*` / `MySQL*` |
| 低延迟会话/缓存 | Redis | `RedisConversationStore` / `RedisAgentMemory` |
| 多租户隔离 | 每行带 `tenant_id`/`user_id`，查询强制带作用域条件（应用层 where 或数据库 RLS） | 所有查询强制带 `user_id`（默认）或 `tenant_id`（组织共享） |
| 本地测试 | 内存态、零配置 | 保留 `DemoAgentMemory`，并新增可选 `user_scoped` |

设计原则：**隔离是查询的强制条件，而不是调用方自觉过滤**。接口方法全部以 `user`/`context` 为作用域入参，实现层在 SQL/Key 中直接带上作用域，避免越权。

---

## 3. 方案设计

### 3.1 后端分层

```
ConversationStore (core/storage/base.py)
├── MemoryConversationStore          (已有，默认，内存)
├── FileSystemConversationStore      (已有，文件)
├── SQLiteConversationStore          (新增)
├── PostgresConversationStore        (新增)
├── MySQLConversationStore           (新增)
└── RedisConversationStore           (新增)

AgentMemory (capabilities/agent_memory/base.py)
├── DemoAgentMemory                  (已有，内存；新增 user_scoped 开关)
├── SQLiteAgentMemory                (新增)
├── PostgresAgentMemory              (新增)
├── MySQLAgentMemory                 (新增)
└── RedisAgentMemory                 (新增)
```

四个持久化后端实现同一组接口，可自由替换；驱动均为**懒加载**，未安装对应驱动时不影响 `import vanna`，只有真正选用该后端时才提示安装 extra。

### 3.2 多租户模型

- 存储行/键中包含 `user_id` 与 `tenant_id`。
- 默认 `share_within_tenant=False`：按 `user_id` 隔离（每个用户只能看到自己的数据）。
- `share_within_tenant=True` + `tenant_resolver`：按 `tenant_id` 隔离（同一组织/租户内共享，跨租户不可见）。

```python
from vanna import User

def tenant_resolver(user: User) -> str:
    return user.metadata.get("org_id", user.id)
```

### 3.3 数据模型扩展

`ToolMemory` / `TextMemory` 增加两个可选字段（向后兼容）：

```python
class ToolMemory(BaseModel):
    ...
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
```

### 3.4 配置化（灵活选择）

新增 `vanna.config.StorageConfig` 与工厂，支持四种来源：

1. Python 参数；
2. 服务端 JSON 配置的 `storage` 段；
3. 环境变量；
4. CLI 参数。

`StorageConfig` 支持：
- `backend`：`memory | sqlite | postgres | mysql | redis`
- `conversation_backend` / `memory_backend`：分别覆盖会话与记忆后端；
- `url`：连接串（postgres/mysql/redis），当 `backend` 仍是默认 `memory` 时可按 URL scheme 自动推断；
- `sqlite_path`：SQLite 文件路径；
- `tenant_sharing` / `tenant_resolver`；
- `options`：透传给具体后端的额外参数。

---

## 4. 代码变动清单

### 4.1 新增文件

| 文件 | 说明 |
|---|---|
| `src/vanna/integrations/sqlite/conversation_store.py` | `SQLiteConversationStore`：标准库 `sqlite3`，WAL、外键级联、`asyncio.to_thread` + `RLock` |
| `src/vanna/integrations/sqlite/agent_memory.py` | `SQLiteAgentMemory`：工具/文本记忆，按作用域过滤，Jaccard+difflib 检索 |
| `src/vanna/integrations/postgres/conversation_store.py` | `PostgresConversationStore`（psycopg2，`ON CONFLICT`，`asyncio.to_thread`） |
| `src/vanna/integrations/postgres/agent_memory.py` | `PostgresAgentMemory` |
| `src/vanna/integrations/mysql/conversation_store.py` | `MySQLConversationStore`（PyMySQL，`ON DUPLICATE KEY UPDATE`） |
| `src/vanna/integrations/mysql/agent_memory.py` | `MySQLAgentMemory` |
| `src/vanna/integrations/redis/conversation_store.py` | `RedisConversationStore`（hash + list + zset，`redis.asyncio`） |
| `src/vanna/integrations/redis/agent_memory.py` | `RedisAgentMemory` |
| `src/vanna/config/storage.py` | `StorageConfig`、`create_stores()`、`create_conversation_store()`、`create_agent_memory()`、`apply_storage_config()` |
| `tests/test_persistence.py` | 持久化 / 隔离测试（11 个用例） |
| `tests/test_storage_config.py` | 配置解析 / 工厂分发 / 外部后端集成测试（12 passed + 3 skipped） |

### 4.2 修改文件

| 文件 | 改动 |
|---|---|
| `src/vanna/capabilities/agent_memory/models.py` | `ToolMemory` / `TextMemory` 增加 `user_id`、`tenant_id` |
| `src/vanna/integrations/local/agent_memory/in_memory.py` | `DemoAgentMemory(user_scoped=False)`；保存时写入归属，开启后按用户过滤检索/列表/删除/清空 |
| `src/vanna/integrations/sqlite/__init__.py` 等 | 导出新后端 |
| `src/vanna/integrations/__init__.py` | 导出 SQLite/Postgres/MySQL/Redis 后端 |
| `src/vanna/config/__init__.py` | 导出 `StorageConfig` 与工厂 |
| `src/vanna/__init__.py` | 顶层导出 `SQLiteConversationStore` / `SQLiteAgentMemory` / `StorageConfig` / `create_stores` 等 |
| `src/vanna/servers/fastapi/app.py` | 构造 server 时若存在 `config["storage"]`，调用 `apply_storage_config` 原地替换 Agent 存储 |
| `src/vanna/servers/flask/app.py` | 同上 |
| `src/vanna/servers/cli/server_runner.py` | 新增 `--storage-backend` / `--storage-url` / `--storage-sqlite-path` / `--storage-tenant-sharing`；支持 `VANNA_STORAGE_BACKEND` 环境变量 |
| `pyproject.toml` | 新增 `redis = ["redis>=4.5.0"]` extra |

### 4.3 关键实现片段

**作用域过滤（以 SQLite 为例）**

```python
def _scope(self, user: User) -> Tuple[str, Any]:
    if self._share_within_tenant:
        return "tenant_id", self._tenant_of(user)
    return "user_id", user.id

def _get_sync(self, conversation_id, user):
    scope_col, scope_val = self._scope(user)
    row = self._conn.execute(
        f"SELECT * FROM vanna_conversations WHERE id = ? AND {scope_col} = ?",
        (conversation_id, scope_val),
    ).fetchone()
    return self._load_conversation(row) if row else None
```

**异步桥接（同步驱动不阻塞事件循环）**

```python
async def get_conversation(self, conversation_id, user):
    return await asyncio.to_thread(self._get_sync, conversation_id, user)
```

**Redis 数据结构**

```
vanna:conv:{id}                 hash   会话元数据 + 归属
vanna:conv:{id}:messages        list   消息 JSON（追加）
vanna:user:{uid}:convs          zset   用户会话索引（score=更新时间）
vanna:tool_mem:{id}             hash   工具使用记忆
vanna:user:{uid}:tool_mems      zset   索引
vanna:text_mem:{id}             hash   文本记忆
vanna:user:{uid}:text_mems      zset   索引
```

**配置工厂**

```python
def create_conversation_store(config=None) -> ConversationStore:
    cfg = _coerce_config(config).validate()
    backend = cfg.resolved_conversation_backend()
    if backend == "sqlite":
        from ..integrations.sqlite import SQLiteConversationStore
        return SQLiteConversationStore(cfg.sqlite_path, **_common_kwargs(cfg))
    ...
```

---

## 5. 使用方式

### 5.1 Python 显式配置

```python
from vanna import Agent, StorageConfig, create_stores

conversation_store, agent_memory = create_stores(
    StorageConfig(backend="postgres", url="postgresql://user:pass@host:5432/vanna")
)
agent = Agent(
    llm_service=...,
    tool_registry=...,
    user_resolver=...,
    conversation_store=conversation_store,
    agent_memory=agent_memory,
)
```

### 5.2 本地 SQLite（推荐默认）

```python
conversation_store, agent_memory = create_stores(
    StorageConfig(backend="sqlite", sqlite_path="vanna.db")
)
```

### 5.3 组织内共享（多租户高级用法）

```python
cfg = StorageConfig(
    backend="redis",
    url="redis://:password@host:6379/0",
    tenant_sharing=True,
    tenant_resolver=lambda u: u.metadata.get("org_id", u.id),
)
conversation_store, agent_memory = create_stores(cfg)
```

### 5.4 服务端 JSON 配置

```json
{
  "storage": {
    "backend": "sqlite",
    "sqlite_path": "vanna.db",
    "tenant_sharing": false
  }
}
```

`VannaFastAPIServer` / `VannaFlaskServer` 会在构造时自动应用到 Agent。

### 5.5 环境变量

```bash
VANNA_STORAGE_BACKEND=postgres
VANNA_STORAGE_URL=postgresql://user:pass@host:5432/vanna
VANNA_STORAGE_TENANT_SHARING=true
```

```python
from vanna.config import StorageConfig
cfg = StorageConfig.from_env()
```

### 5.6 CLI

```bash
vanna --storage-backend sqlite --storage-sqlite-path vanna.db
vanna --storage-backend postgres --storage-url postgresql://user:pass@host/db
vanna --storage-backend redis --storage-url redis://:pass@host:6379/0 --storage-tenant-sharing
```

### 5.7 保留本地测试方式

```python
from vanna.integrations.local.agent_memory import DemoAgentMemory

# 旧行为：所有用户共享（默认，兼容既有 demo/测试）
memory = DemoAgentMemory()

# 需要多用户隔离时：
memory = DemoAgentMemory(user_scoped=True)
```

---

## 6. 验证

- `tests/test_persistence.py`（11 passed）
  - SQLite 会话跨实例往返、用户隔离、租户共享、删除级联；
  - SQLite 记忆往返、用户隔离、租户共享、删除/清空按作用域；
  - `DemoAgentMemory` 默认共享行为不变、`user_scoped=True` 隔离生效。
- `tests/test_storage_config.py`（12 passed + 3 skipped）
  - `StorageConfig` 字典/环境变量解析、URL scheme 自动推断、非法后端校验、MySQL URL 解析；
  - 工厂分发（memory/sqlite）、`apply_storage_config`；
  - Postgres/MySQL/Redis 集成测试由 `VANNA_TEST_*_URL` 环境变量启用（默认跳过）。
- 真实 Redis 实例上额外验证过：会话创建/追加/读回、用户隔离、记忆保存/检索、新实例读回、清理归零。
- 相关回归：`test_memory_tools`、`test_workflow`、`test_tool_permissions` 等全部通过。

---

## 7. 兼容性与注意事项

1. **默认行为不变**：`Agent` 未显式传 `conversation_store` 时仍是 `MemoryConversationStore`；`DemoAgentMemory` 默认仍是共享语义。只有显式选择持久化后端或开启 `user_scoped` 才会改变行为。
2. **SQLite 连接生命周期**：`SQLite*` 实例持有连接，长期运行无需关闭；测试/短生命周期脚本建议显式 `store.close()`。
3. **Postgres/MySQL 连接方式**：当前实现为「每次操作开连接」，功能正确但高并发下建议后续引入连接池或换用异步驱动。
4. **Redis 检索**：`RedisAgentMemory` 的相似度检索会把作用域内全部记忆取回后在本地打分，记忆量很大时应换成向量库后端。
5. **隔离语义**：默认按 `user_id`；`share_within_tenant=True` 时同租户用户共享数据，请确认 `tenant_resolver` 的取值符合业务预期。

---

## 8. 后续可扩展点

- 为 Postgres/MySQL 增加连接池（或异步驱动 asyncpg/aiomysql）；
- 增加 Alembic/SQL 迁移脚本管理表结构版本；
- 为 Redis 记忆检索增加 `scan_limit` / FTS，避免全量拉取；
- 增加数据库级 RLS（Postgres Row Level Security）作为第二道隔离。

---

## 附：提交与文件

- 提交：`c13633d`
- 主要目录：`src/vanna/integrations/{sqlite,postgres,mysql,redis}/`、`src/vanna/config/storage.py`
- 测试：`tests/test_persistence.py`、`tests/test_storage_config.py`
