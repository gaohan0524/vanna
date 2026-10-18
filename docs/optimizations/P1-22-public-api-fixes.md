# P1-22 修复公开 API（register / create_basic_agent / __all__ / 版本号）

- **对应问题**：`ToolRegistry.register()` 不存在、`create_basic_agent` 未实现、顶层 `__all__` 导出未导入的名字、版本号不一致
- **优先级**：P1（开箱即用）
- **状态**：已完成
- **提交**：`3c4c958 feat: 收紧 SQL 默认值并修复公开 API（P1-21/22/23）`

---

## 1. 问题背景

四处「开箱即坏」：

1. 示例/早期文档使用 `tool_registry.register(tool)`，但 `ToolRegistry` 只有 `register_local_tool`，直接 `AttributeError`；
2. CLI 默认分支 `from ...agents import create_basic_agent`，但 `agents/__init__.py` 是空的（`__all__ = []`），`vanna` 不带 `--example` 必失败；
3. 顶层 `vanna/__init__.py` 的 `__all__` 里列了 `VannaFlaskServer` / `VannaFastAPIServer` / `ChatHandler` / `ChatRequest` / `ChatStreamChunk` / `ExampleAgentLoader`，但文件内从未 import，`from vanna import X` → `ImportError`；
4. `__version__ = "0.1.0"` 与 `pyproject.toml` 的 `2.0.2` 不一致。

---

## 2. 方案设计

| 问题 | 修复 |
|---|---|
| `register` 不存在 | `ToolRegistry` 新增 `register(tool, access_groups=None)`，内部委托 `register_local_tool` |
| `create_basic_agent` 缺失 | 新增 `agents/__init__.py`：`create_basic_agent(llm_service)` 用极简 cookie 解析器 + `DemoAgentMemory` 构造最小 Agent |
| `__all__` 名字未导入 | `ChatHandler`/`ChatRequest`/`ChatStreamChunk`/`ExampleAgentLoader` 顶层直接导入；`VannaFastAPIServer`/`VannaFlaskServer` 用模块级 `__getattr__` 懒加载（避免 `import vanna` 强依赖 fastapi/flask） |
| 版本号不一致 | `__version__` 改为从 `importlib.metadata` 读取 |

### 2.1 `register` 兼容入口

```python
def register(self, tool, access_groups=None):
    self.register_local_tool(tool, list(access_groups or []))
```

### 2.2 `create_basic_agent`

```python
class _DemoUserResolver(UserResolver):
    async def resolve_user(self, request_context):
        email = (request_context.cookies or {}).get("vanna_email", "guest@example.com")
        return User(id=email, email=email, group_memberships=["user"])

def create_basic_agent(llm_service, *, tool_registry=None, user_resolver=None, agent_memory=None):
    return Agent(
        llm_service=llm_service,
        tool_registry=tool_registry or ToolRegistry(),
        user_resolver=user_resolver or _DemoUserResolver(),
        agent_memory=agent_memory or DemoAgentMemory(),
    )
```

### 2.3 懒加载 server 类

```python
def __getattr__(name):
    if name == "VannaFastAPIServer":
        from .servers.fastapi.app import VannaFastAPIServer
        return VannaFastAPIServer
    if name == "VannaFlaskServer":
        from .servers.flask.app import VannaFlaskServer
        return VannaFlaskServer
    raise AttributeError(...)
```

### 2.4 版本号

```python
try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("vanna")
except Exception:
    __version__ = "0.0.0"
```

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `core/registry.py` | 新增 `register()` |
| `agents/__init__.py`（新增） | `create_basic_agent` + `_DemoUserResolver` |
| `__init__.py` | 顶层导入服务器组件；`__getattr__` 懒加载两个 server；`__version__` 读包元数据 |
| `tests/test_public_api_defaults.py` | 新增 `test_registry_register_alias` / `test_create_basic_agent` / `test_public_api_names_and_version` |

---

## 4. 使用方式

```python
from vanna.agents import create_basic_agent
agent = create_basic_agent(llm_service)

from vanna import ChatHandler, VannaFastAPIServer   # 均可正常导入
import vanna
print(vanna.__version__)   # 2.0.2（与 pyproject 一致）
```

---

## 5. 验证

- `test_registry_register_alias`：`registry.register(...)` 后 `list_tools` 含该工具；
- `test_create_basic_agent`：返回 `Agent` 且 resolver/memory/tool_registry 均已注入；
- `test_public_api_names_and_version`：`__version__ == "2.0.2"`，四个服务器组件与懒加载的 `VannaFastAPIServer` 均可导入。
- 回归：核心测试集 **174 passed, 3 skipped**。

---

## 6. 兼容性与注意事项

1. **`register` 语义**：与 `register_local_tool(tool, [])` 等价（所有用户可见）；需要权限限制请显式传 `access_groups`。
2. **懒加载**：`VannaFlaskServer` 在未安装 flask 时访问会抛 `ImportError`（符合预期）；`VannaFastAPIServer` 需要 fastapi。
3. **版本号**：依赖 `vanna` 已安装为发行版；源码 checkout 未安装时回退 `0.0.0`。
4. **`__getattr__` 注意**：任何未定义的顶层属性访问都会走它并抛 `AttributeError`，不影响正常导入。

---

## 7. 后续可扩展点

- 为 `create_basic_agent` 增加 `conversation_store` / `observability_provider` 等可选注入；
- 将 `register` 标记为 `@deprecated` 并指向 `register_local_tool`；
- 用 `pyproject` 版本号自动同步 `__version__`（已通过 `importlib.metadata` 实现）。

---

## 附：文件

- 修改：`src/vanna/core/registry.py`、`src/vanna/__init__.py`；新增 `src/vanna/agents/__init__.py`
- 测试：`tests/test_public_api_defaults.py`
