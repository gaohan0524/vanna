# P1-13 本地文件 I/O 非阻塞化

- **对应问题**：#13 同步文件 I/O 在 async 方法里阻塞事件循环
- **优先级**：P1（正确性/性能隐患）
- **状态**：已完成
- **提交**：本次修改（P1-13，见 `git log`）

---

## 1. 问题背景

`LocalFileSystem` 与 `FileSystemConversationStore` 的公开方法都是 `async def`，但内部直接调用同步文件 API：

```python
# LocalFileSystem（优化前）
async def read_file(self, filename, context):
    file_path = self._resolve_path(filename, context)
    return file_path.read_text(encoding="utf-8")   # 阻塞

# FileSystemConversationStore（优化前）
async def get_conversation(self, conversation_id, user):
    with open(metadata_path, "r") as f:            # 阻塞
        metadata = json.load(f)
    ...
```

`async def` 只让函数返回协程，**不会让内部同步调用变成非阻塞**。磁盘慢、文件大、网络盘等场景下，这些调用会占住事件循环线程，导致同一循环上的聊天请求、WebSocket 心跳、健康检查全部停摆。

这与 P0-01「LLM 同步阻塞 SDK」是同一类问题，只是发生在文件系统层。

---

## 2. 主流方案参考

| 方案 | 说明 |
|---|---|
| `asyncio.to_thread` / `run_in_executor` | 把阻塞 I/O 丢到线程池，事件循环继续调度 |
| `aiofiles` | 基于线程池的异步文件封装，本质同上 |
| 异步文件系统驱动 | 如 `anyio` 的异步文件 API、对象存储 SDK 的异步客户端 |
| 异步数据库/存储客户端 | Redis/Postgres 等已有异步驱动（本项目已采用） |

本项目选择 `asyncio.to_thread`：**零新增依赖**，与已有 `SQLiteConversationStore` 的做法保持一致。

---

## 3. 方案设计

### 3.1 同步实现 + 异步门面

把每个方法的阻塞逻辑下沉为同步的 `_*_sync` 私有方法，公开的 async 方法只负责 `await asyncio.to_thread(...)`：

```python
async def read_file(self, filename, context) -> str:
    return await asyncio.to_thread(self._read_file_sync, filename, context)

def _read_file_sync(self, filename, context) -> str:
    ...  # 原有同步逻辑
```

好处：

- 逻辑与异常语义完全不变，只是执行线程变了；
- 测试可以直接对 `_*_sync` 做 monkeypatch，验证「是否真的 offload」；
- 不影响 `FileSystem` / `ConversationStore` 抽象接口。

### 3.2 `LocalFileSystem`

覆盖所有涉及磁盘的方法：

| 方法 | 是否阻塞 | 处理 |
|---|---|---|
| `list_files` | `exists` / `is_dir` / `iterdir` | `_list_files_sync` + `to_thread` |
| `read_file` | `exists` / `read_text` | `_read_file_sync` + `to_thread` |
| `write_file` | `mkdir` / `exists` / `write_text` | `_write_file_sync` + `to_thread` |
| `exists` | `resolve` / `exists` | `_exists_sync` + `to_thread` |
| `is_directory` | `resolve` / `is_dir` | `_is_directory_sync` + `to_thread` |
| `search_files` | `rglob` / `stat` / `read_text` | `_search_files_sync` + `to_thread` |
| `run_bash` | 本身异步（subprocess） | 仅把 `mkdir` 的用户目录解析放进 `to_thread` |

### 3.3 `FileSystemConversationStore`

五个 CRUD 方法全部改为「同步实现 + 异步门面」：

```python
async def get_conversation(self, conversation_id, user):
    return await asyncio.to_thread(
        self._get_conversation_sync, conversation_id, user
    )
```

内部 `delete_conversation_sync` 调用 `_get_conversation_sync`（同步调用，不再 `await`），保持原有归属校验逻辑。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/integrations/local/file_system.py` | 新增 6 个 `_*_sync` 同步实现；公开 async 方法通过 `asyncio.to_thread` 调用；`run_bash` 的 `mkdir` 也 offload |
| `src/vanna/integrations/local/file_system_conversation_store.py` | 新增 `_create/_get/_update/_delete/_list_*_sync`；公开 async 方法通过 `asyncio.to_thread` 调用 |
| `tests/test_local_io_nonblocking.py` | 新增 5 个用例：功能往返 + 事件循环不阻塞 |

### 关键代码

```python
# LocalFileSystem
async def list_files(self, directory, context) -> List[str]:
    return await asyncio.to_thread(self._list_files_sync, directory, context)

# FileSystemConversationStore
async def list_conversations(self, user, limit=50, offset=0):
    return await asyncio.to_thread(
        self._list_conversations_sync, user, limit, offset
    )
```

---

## 5. 使用方式

对外 API 与行为完全不变：

```python
fs = LocalFileSystem(working_directory=".")
await fs.write_file("report.csv", csv_text, context, overwrite=True)
content = await fs.read_file("report.csv", context)

store = FileSystemConversationStore(base_dir="conversations")
conv = await store.get_conversation("c1", user)
```

> 若追求更高吞吐，可继续替换为 `aiofiles` 或对象存储的异步客户端；框架接口不变。

---

## 6. 验证

`tests/test_local_io_nonblocking.py`（5 passed）：

- `LocalFileSystem` 功能往返：写/读/存在性/目录判断/列表/搜索/覆盖写；
- `read_file` 非阻塞：把 `_read_file_sync` 替换为 `sleep(0.2)`，同时跑一个 10ms ticker，断言 ticker 至少推进 5 次；
- `search_files` 非阻塞：同上；
- `FileSystemConversationStore` 功能往返：创建/读取/追加/列表/用户隔离/删除；
- `get_conversation` 非阻塞：把 `_get_conversation_sync` 替换为 `sleep(0.2)`，断言 ticker 持续推进。

回归：相关测试集 **166 passed, 3 skipped**。

---

## 7. 兼容性与注意事项

1. **行为不变**：方法签名、返回值、异常类型均未改变。
2. **线程池**：默认使用 `asyncio` 的默认线程池；高并发大量文件操作时可考虑自定义 `ThreadPoolExecutor` 并限制并发。
3. **并发写同一文件**：offload 只解决「阻塞事件循环」，不解决「多个协程同时写同一文件」的竞态；需要时自行加锁或使用原子写（临时文件 + rename）。
4. **线程安全**：`LocalFileSystem` 无共享可变状态；`FileSystemConversationStore` 的并发写仍需业务层保证。
5. **Windows**：文件句柄释放依赖 with/上下文；测试中临时目录清理可能因句柄未释放而失败（与本次改动无关）。

---

## 8. 后续可扩展点

- 用 `aiofiles` 替换 `to_thread`，进一步降低线程池开销；
- 为 `FileSystemConversationStore` 增加原子写与文件锁；
- 为 `LocalFileSystem.search_files` 增加超时与最大遍历文件数限制；
- 数据库类存储（Postgres/MySQL）引入连接池，减少每次操作建连开销。

---

## 附：文件

- 修改：
  - `src/vanna/integrations/local/file_system.py`
  - `src/vanna/integrations/local/file_system_conversation_store.py`
- 测试：`tests/test_local_io_nonblocking.py`
