# P1-10 结构化输出约束 + SQL 校验

- **对应问题**：#10 Text2SQL 无结构化输出约束
- **提交**：`c0a269a feat: 结构化输出约束与 SQL 校验（strict tools + response_format）`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前：

- `RunSqlTool` 的参数虽然由 Pydantic 模型生成 JSON Schema，但**未启用供应商的严格模式**（OpenAI `strict` / Gemini `response_schema` 等）；
- 框架层没有 `response_format` 能力，无法请求结构化输出；
- SQL 字符串本身没有结构校验（只依赖 prompt 引导 + 事后 Pydantic 校验）；
- 只读检查是正则启发式，且没有拒绝多语句/超长 SQL。

需要区分两层「结构化」：

| 层次 | 能约束什么 |
|---|---|
| JSON Schema / strict tool calling | **工具调用 JSON 的形状**（字段、类型、必填、禁止额外字段） |
| SQL 校验（解析器） | **SQL 字符串本身**（单语句、只读、长度、语法） |

只有前者无法保证 SQL 合法，因此本次做了「严格工具体系 + SQL 校验」双层。

---

## 2. 主流方案参考

| 方案 | 能力 |
|---|---|
| OpenAI Structured Outputs / `strict: true` | 强制模型输出符合 JSON Schema 的参数；要求 `additionalProperties: false` + 全字段 required |
| Gemini `response_mime_type` + `response_schema` | JSON 输出 + schema 约束 |
| Ollama `format` | 传 JSON Schema 约束输出 |
| sqlglot / sqlparse | SQL 解析、方言识别、只读判定、单语句校验 |
| 语法约束解码（GBNF/Outlines） | 本地模型逐 token 约束 SQL 语法（框架外能力） |

---

## 3. 方案设计

### 3.1 严格 JSON Schema 生成

Pydantic 默认 schema 不满足 strict 模式要求（存在可选字段、`default` 等）。新增转换器：

```python
def make_strict_json_schema(schema: dict) -> dict:
    strict = copy.deepcopy(schema)
    _strictify(strict)
    return strict

def _strictify(node):
    if isinstance(node, dict):
        node.pop("default", None)
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["additionalProperties"] = False
            node["required"] = list(properties.keys())
        for value in node.values():
            _strictify(value)
    elif isinstance(node, list):
        for item in node:
            _strictify(item)
```

效果：

- 每个 object 加 `additionalProperties: false`；
- 所有属性进入 `required`；
- 移除 `default`；
- 原始 schema 不被修改（深拷贝）。

### 3.2 工具 schema 支持 strict

```python
# Tool.get_schema(strict: bool = False)
schema = args_model.model_json_schema()
if strict:
    schema = make_strict_json_schema(schema)
return ToolSchema(name=..., parameters=schema, strict=strict)

# ToolRegistry.get_schemas(user, *, strict=False)
schemas.append(tool.get_schema(strict=strict))
```

`ToolSchema` 新增 `strict` 字段。

### 3.3 配置与透传

```python
class AgentConfig(BaseModel):
    strict_tools: bool = False
    response_format: Optional[Dict[str, Any]] = None

class LlmRequest(BaseModel):
    strict_tools: bool = False
    response_format: Optional[Dict[str, Any]] = None
```

`Agent._build_llm_request` 自动透传；`_send_message` 调用 `get_schemas(user, strict=self.config.strict_tools)`。

### 3.4 供应商适配

| 适配器 | 映射 |
|---|---|
| OpenAI | 工具加 `"strict": true`；`response_format` 原样透传 |
| Ollama | `response_format` → `format`（`"json"` 或 JSON Schema） |
| Gemini | `response_format` → `response_mime_type="application/json"` + `response_schema` |
| Anthropic | 无原生 JSON Schema 模式，保持跳过（可用 tool use 模拟） |

### 3.5 SQL 结构化校验

新增 `vanna/tools/sql_validation.py`：

```python
def validate_sql(
    sql,
    *,
    read_only=False,
    max_length=None,
    require_single_statement=True,
) -> SqlValidationResult:
    ...
```

校验项与 `error_type`：

| 校验 | `error_type` |
|---|---|
| 空语句 | `empty_sql` |
| 超过最大长度 | `sql_too_long` |
| 多语句（堆叠注入） | `multiple_statements` |
| 只读模式写操作 | `read_only_violation` |

`RunSqlTool.execute` 在执行前调用 `validate_sql`，失败时把结构化原因回喂 LLM：

```python
validation = validate_sql(
    args.sql,
    read_only=self.read_only,
    max_length=self.max_sql_length,
    require_single_statement=self.require_single_statement,
)
if not validation.ok:
    return ToolResult(
        success=False,
        result_for_llm=validation.reason,
        metadata={"error_type": validation.error_type or "invalid_sql"},
    )
```

`RunSqlTool` 新增参数：

- `max_sql_length: Optional[int]`（默认 None）
- `require_single_statement: bool`（默认 True）

---

## 4. 代码变动清单

### 4.1 新增文件

| 文件 | 说明 |
|---|---|
| `src/vanna/core/llm/schema.py` | `make_strict_json_schema` |
| `src/vanna/tools/sql_validation.py` | `validate_sql` / `split_statements` / `_is_read_only_query` |
| `tests/test_structured_output.py` | 15 个用例 |

### 4.2 修改文件

| 文件 | 改动 |
|---|---|
| `src/vanna/core/llm/models.py` | `LlmRequest` 增加 `strict_tools` / `response_format` |
| `src/vanna/core/llm/__init__.py` | 导出 `make_strict_json_schema` |
| `src/vanna/core/tool/models.py` | `ToolSchema` 增加 `strict` |
| `src/vanna/core/tool/base.py` | `get_schema(strict=False)` |
| `src/vanna/core/registry.py` | `get_schemas(user, *, strict=False)` |
| `src/vanna/core/agent/config.py` | `AgentConfig.strict_tools` / `response_format` |
| `src/vanna/core/agent/agent.py` | 严格 schema 获取 + `LlmRequest` 透传 |
| `src/vanna/integrations/openai/llm.py` | `strict` 工具 + `response_format` |
| `src/vanna/integrations/ollama/llm.py` | `response_format` → `format` |
| `src/vanna/integrations/google/gemini.py` | `response_mime_type` / `response_schema` |
| `src/vanna/tools/run_sql.py` | 接入 `validate_sql`；新增 `max_sql_length` / `require_single_statement` |
| `src/vanna/tools/__init__.py` | 导出 `validate_sql` / `SqlValidationResult` |

### 关键代码

```python
# 严格 schema
strict = tool.get_schema(strict=True)
assert strict.parameters["additionalProperties"] is False
assert set(strict.parameters["required"]) == set(strict.parameters["properties"])

# OpenAI payload
function["strict"] = True
payload["response_format"] = request.response_format

# SQL 校验
result = validate_sql("SELECT 1; DROP TABLE t")
assert result.error_type == "multiple_statements"
```

---

## 5. 使用方式

```python
from vanna import Agent, AgentConfig

agent = Agent(
    ...,
    config=AgentConfig(
        strict_tools=True,          # 启用严格工具调用
        # response_format={"type": "json_object"},  # 需要 JSON 输出时开启
    ),
)
```

```python
from vanna.tools import RunSqlTool

db_tool = RunSqlTool(
    sql_runner=...,
    read_only=True,
    require_single_statement=True,  # 拒绝堆叠语句
    max_sql_length=20000,
)
```

> 注意：对 Text2SQL 一般**不建议**全局设置 `response_format`（最终答案是自然语言）；SQL 的结构约束主要靠 `strict_tools` + 工具 schema + `validate_sql`。

---

## 6. 验证

`tests/test_structured_output.py`（15 passed）：

- `make_strict_json_schema`：嵌套 object、`additionalProperties`、全字段 required、移除 default、不修改原 schema；
- `Tool.get_schema(strict=True)` / `ToolRegistry.get_schemas(strict=True)`；
- Agent 透传：`LlmRequest.strict_tools` / 工具 schema 严格化；
- 未开启时默认行为不变；
- OpenAI payload `strict` + `response_format`；Ollama `format` 翻译；
- `validate_sql`：空/多语句/尾分号/只读/超长；
- `RunSqlTool` 在执行前拦截多语句，且 runner 未被调用。

回归：`tests/test_run_sql_guardrails.py` 全部通过。

---

## 7. 兼容性与注意事项

1. **默认关闭**：`strict_tools=False`，不改变既有行为。
2. **schema 兼容性**：`make_strict_json_schema` 保留 `anyOf` / `$ref` / `$defs`；若模型/供应商对 `Optional` 字段的 `anyOf` 支持有限，建议工具参数尽量使用必填字段。
3. **JSON Schema ≠ SQL 语法**：strict 模式只保证参数形状；SQL 合法性仍需 `validate_sql` 或 sqlglot/AST 校验。
4. **单语句限制**：`require_single_statement=True` 会拒绝 `;` 分隔的多语句（安全默认）；如确需执行多语句，显式关闭。
5. **供应商差异**：Anthropic 无原生 JSON Schema 模式；Ollama 的 `format` 与 tools 同时使用需确认模型支持。
6. **错误回喂**：校验失败以 `ToolResult(success=False)` 返回，LLM 可在下一轮自我修复。

---

## 8. 后续可扩展点

- 接入 `sqlglot` 做 AST 级校验、方言识别、表/列存在性检查；
- 为 `validate_sql` 增加「禁止全表扫描」「必须带 LIMIT」等策略；
- 对本地模型接入 GBNF/Outlines 做语法约束解码；
- 将 `response_format` 下沉到工具级（不同工具不同 schema）。

---

## 附：提交与文件

- 提交：`c0a269a`
- 新增：`src/vanna/core/llm/schema.py`、`src/vanna/tools/sql_validation.py`
- 测试：`tests/test_structured_output.py`
