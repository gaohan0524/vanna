# P0-03 跨用户会话越权与路径穿越

- **对应问题**：客户端传入的 `conversation_id` 未被校验归属，可向他人会话注入消息或接管会话
- **优先级**：P0（安全）
- **状态**：已完成
- **提交**：`99fd483 fix: 修复跨用户会话越权与路径穿越（P0-A）`

---

## 1. 问题背景

优化前，`Agent.send_message` 对客户端传入的 `conversation_id` **无条件信任**：

```python
conversation = await self.conversation_store.get_conversation(conversation_id, user)
is_new_conversation = conversation is None
if not conversation:
    # 用客户端给的 id 直接新建会话
    conversation = Conversation(id=conversation_id, user=user, messages=[])
```

随后 `update_conversation` 把该会话 upsert 到存储。各存储的 update 是**不带 `user_id` 条件**的 upsert：

- SQLite/Postgres：`INSERT ... ON CONFLICT(id) DO UPDATE`，B 的消息按 seq 追加进 A 的会话；
- MySQL：`ON DUPLICATE KEY UPDATE`，同理；
- Redis：`hgetall` 无归属校验即写入；
- 文件后端：`_save_metadata` 直接覆盖 `metadata.json` 的 `user` 字段。

**后果**：攻击者 B 用 A 的 `conversation_id` 发消息，即可把自己的消息塞进 A 的会话（A 后续能看到），文件后端甚至直接**接管**会话并读取全部历史。同时文件后端的 id 未做路径穿越校验，`../` 可逃逸存储目录。

---

## 2. 主流方案参考

| 方案 | 做法 |
|---|---|
| 多租户 SaaS（Postgres RLS） | 行级安全策略：`USING (user_id = current_user_id)`，越权读/写自动过滤/报错 |
| LangChain / 主流 Agent 框架 | 会话 id 服务端生成，客户端 id 仅作「继续会话」引用，归属不符则新建 |
| 文件存储 | 对路径组件做白名单校验 + `resolve()` 后必须在基目录内 |

核心原则：**按身份作用域是查询/写入的强制条件，而非调用方自觉**；客户端提供的 id 只是「定位已有会话」的线索，不能作为「创建」的授权凭证。

---

## 3. 方案设计

双层防御：

### 3.1 传输层（`ChatHandler`，不可信输入边界）

新增 `_resolve_conversation_id`：客户端 id **仅在「已存在且属于当前用户」时复用**，否则生成新的服务端 id。

```python
async def _resolve_conversation_id(self, request) -> str:
    requested = request.conversation_id
    if not requested or request.request_context is None:
        return self._generate_conversation_id()
    try:
        user = await self.agent.user_resolver.resolve_user(request.request_context)
        existing = await self.agent.conversation_store.get_conversation(requested, user)
    except Exception:
        return self._generate_conversation_id()
    if existing is None:
        return self._generate_conversation_id()   # 未归属，重新生成
    return requested
```

选在 `ChatHandler` 而非 `Agent` 里做，是因为 `ChatHandler` 同时负责把最终 id 写进每个 `ChatStreamChunk`——在这里换 id，客户端拿到的 `conversation_id` 才是正确的，避免「Agent 内换 id 而 chunk 仍回旧 id」的错位。

### 3.2 存储层（强制作用域校验，防御纵深）

| 存储 | 校验方式 |
|---|---|
| SQLite / Postgres | `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` + `UPDATE ... WHERE id=? AND {scope_col}=?`，`rowcount==0` 即报错 |
| MySQL | 先 `SELECT` 判断存在性与归属，不匹配即 `rollback` + 报错（避免 rowcount 语义差异） |
| Redis | 写入前 `hgetall` 校验 `user_id`/`tenant_id`，不匹配即报错 |
| 文件后端 | id 白名单 `^[A-Za-z0-9_-]+$` + `resolve().relative_to(base_dir)` 双重校验 |

### 3.3 `create_conversation` 改为纯 INSERT

所有存储的 `create_conversation` 由「破坏性 upsert（`OR REPLACE` + 清空消息）」改为**纯 INSERT，id 已存在即抛 `ValueError`**，杜绝清空历史/重指派归属。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `servers/base/chat_handler.py` | 新增 `_resolve_conversation_id`；`handle_stream` 调用之 |
| `integrations/sqlite/conversation_store.py` | `update` 带作用域 `UPDATE` + rowcount 校验；`create` 纯 INSERT |
| `integrations/postgres/conversation_store.py` | 同上 |
| `integrations/mysql/conversation_store.py` | `update` SELECT 归属校验；`create` 纯 INSERT |
| `integrations/redis/conversation_store.py` | `update`/`create` 前 `hgetall` 归属校验 |
| `integrations/local/file_system_conversation_store.py` | id 白名单 + `resolve()` 校验；`update`/`create` 归属/存在性校验 |
| `tests/test_conversation_isolation.py` | 新增 4 个用例 |

---

## 5. 验证

`tests/test_conversation_isolation.py`（4 passed）：

- Agent 层：Bob 复用 Alice 的 id 会拿到一个新 id，Alice 会话不被污染；
- 存储层（SQLite/文件后端参数化）：跨用户 `update_conversation` 抛 `ValueError` 且不改数据；重复 `create` 抛错；
- 文件后端路径穿越：`../escape` 抛 `ValueError`；
- Redis 集成用例（真实 Redis 实测）通过。

回归：相关测试集 **255 passed, 3 skipped**（`test_database_sanity.py` 的 18 个失败为环境缺驱动包，与本改动无关）。

---

## 6. 兼容性与注意事项

1. **行为变化**：客户端不能再「预分配」会话 id；未归属的 id 会被服务端替换为新 id（前端应从流式 chunk 的 `conversation_id` 更新本地 id）。
2. **存储 API 变化**：`create_conversation` 对已存在 id 从「静默覆盖」改为抛 `ValueError`。
3. **`Agent` 直接调用**：`Agent.send_message` 仍是可信 API；直接传他人 id 会因存储层作用域校验报错（错误卡片），不会注入成功。
4. **并发**：Redis 的读-校验-写非原子；如需强一致可用 Lua/WATCH，见「后续」。

---

## 7. 后续可扩展点

- Redis 归属校验用 `WATCH`/Lua 脚本做成原子操作；
- 存储层校验失败抛语义异常（如 `ConversationNotFoundError`）而非 `ValueError`；
- 为会话 id 增加「不可枚举」的随机熵（当前 `conv_<8hex>` 偏短）。

---

## 附：文件

- 修改：`servers/base/chat_handler.py`、5 个 `conversation_store.py`
- 测试：`tests/test_conversation_isolation.py`
