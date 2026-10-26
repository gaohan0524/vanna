# Stage 2 执行手册：工具面重构与 SQL 护栏

> 本文是 `docs/AGENT_QUALITY_PLAN.md` 中 **Stage 2 / T2-1 ~ T2-7** 的详细执行版。
>
> - 状态：**未开始**
> - 前置：Stage 1 完成（2026-10-25）
> - 基线：EX **61.9%**（13/21）、behavior **88.9%**（8/9）、平均工具轮次 **1.07~1.23**、P95 **16.5s**
> - 目标：EX ≥ **70%**、`TOOL_MISUSE` < 5%、护栏无绕过、复杂查询 +10pt
> - 最后更新：2026-10-26

---

## 0. 从 Stage 1 继承的事实

### 0.1 基线（`stage1-v4`，30 条）

| 指标 | 值 |
|---|---|
| EX | 61.9%（13/21） |
| 首答命中 | 61.9% |
| behavior | 88.9%（8/9） |
| 平均工具调用 | 1.07~1.23 |
| P95 | 16.5s |
| 提示词 | 2,726 tokens |
| 两次差异 | 0pt |

### 0.2 剩余失败（Stage 2 的输入）

| 用例 | 现象 | 根因 |
|---|---|---|
| C-14 | 每周订单数 | 周窗口/边界仍未对齐 |
| C-15 | 净收入环比 | 模型漏扣退款 |
| C-16 | 12 个月趋势 | 首月环比 / 空月处理 |
| D-04 | 上周转化率 | 有时反问、有时比率/百分比混用 |
| D-17 | 7 日留存 | 模型把“窗口内任意访问”当留存 |
| D-18 | 加购→下单 | 模型算成“全部会话转化率” |
| E-05 | 复购率 | 仍然直接回答，不澄清 |
| I-30 | 30 天环比 | 返回多行对比，golden 是单值 |
| B-02 / C-03 / H-26 | 单条翻转 | 表达形式抖动，总体 EX 稳定 |

这些失败中，**只有一部分能靠工具解决**（探查字段、取值、扫描量）；口径类失败仍要靠 prompt 与 golden。Stage 2 不承诺把它们全部修掉。

### 0.3 Stage 1 已冻结、Stage 2 不改的东西

- 30 条用例与 golden（除发现真实错误）
- 评分规则、自然日窗口、数据锚点语义
- `stage1-v4` 的 L0/L1/L2 结构（只追加工具使用规则，不重写）

---

## 1. 目标与 G2 门禁

### 1.1 目标

1. 模型在**不确定字段/取值/扫描量**时，能先调用工具探查，而不是猜或直接报错。
2. 把 `run_sql` 的护栏从“关键字黑名单”升级为 **AST 只读校验 + LIMIT + 服务端超时**。
3. 工具返回给 LLM 的内容**结构化、可解释**：截断要说明总行数/总列数，错误要带 `error_type` 与修正建议。
4. 不破坏 Stage 1 的收益：EX 不回落，behavior ≥ 85%。

### 1.2 G2 验收

- [ ] EX ≥ **70%**（21 条 answer，至少 15 条）
- [ ] 相对 Stage 1 基线（61.9%）**不回落**；两次同配置差异 ≤ 5pt
- [ ] behavior ≥ **85%**
- [ ] 平均工具轮次 ≤ **3.5**（当前已 1.1，主要防工具滥用）
- [ ] `TOOL_MISUSE` < 5%（人工抽检 20 条轨迹）
- [ ] `run_sql` 绕过测试 100% 拦截（写操作、多语句、超长、注释绕过、字符串字面量）
- [ ] `SCHEMA_MISSING` / `DIALECT_ERROR` = 0
- [ ] 复杂查询（≥3 表 join）成功率 ≥ 60%
- [ ] P95 ≤ **20s**
- [ ] 工具单测覆盖：catalog 搜索、脱敏、LIMIT 注入、超时透传、错误结构

> 统计提醒：21 条 answer 每条≈4.8pt；从 61.9% 到 70% 约 2 条用例。Stage 2 的真实价值更多在“护栏正确性”与“轨迹质量”，不要只盯 EX。

---

## 2. 设计原则

1. **保留 L1 schema**：12 张表只有 2,726 tokens，全量放进提示词仍然划算；工具用于“深挖”，不是替代。
2. **catalog 优先**：`search_columns` / `describe_table` / `get_metric_definition` 只读 `schema_catalog.json` 与 `glossary.yaml`，不查数据库。
3. **只有 sample / explain 查库**：必须强制 `LIMIT`、超时、表名白名单、结果脱敏。
4. **工具描述即路由**：description 写清“什么时候用、什么时候不要用、和谁分工”，比参数名更重要。
5. **护栏在服务端**：只读、LIMIT、超时都由工具构造参数决定，不接受 LLM 参数覆盖。

---

## 3. 目录与文件

```text
analytics_agent/
├── tools/
│   ├── __init__.py
│   ├── search_columns.py      # search_columns
│   ├── describe_table.py      # describe_table
│   ├── sample_rows.py         # sample_rows
│   ├── explain_query.py       # explain_query
│   ├── metric_definition.py   # get_metric_definition
│   └── guardrails.py          # AST 只读校验 / LIMIT 注入
├── context/
│   └── search.py              # catalog 上的纯函数：search / describe
└── agent_factory.py           # 注册工具（access_groups）
tests/
├── test_catalog_tools.py
├── test_sample_tools.py
├── test_metric_tool.py
└── test_sql_guardrails.py
```

新增依赖：`sqlglot>=25`（纯 Python，用于 AST 校验）。加入 `pyproject.toml` 主依赖；若暂时不加，`guardrails.py` 回退到 `sqlparse` 并记录 warning，但 G2 的绕过测试要求 AST 路径。

---

## 4. T2-1：`search_columns` / `describe_table`

### 4.1 catalog 查询函数（`context/search.py`）

```python
def iter_columns(catalog):
    for table in catalog["tables"]:
        for column in table["columns"]:
            yield table, column


def search_columns(catalog, keyword: str, limit: int = 20):
    """按 表名/字段名/注释 模糊匹配；表名命中权重最高。"""
    kw = keyword.strip().lower()
    hits = []
    for table, column in iter_columns(catalog):
        score = 0
        if kw in table["name"].lower():
            score += 3
        if kw in column["name"].lower():
            score += 2
        if kw in (column.get("comment") or "").lower():
            score += 1
        if kw in (table.get("summary") or "").lower():
            score += 1
        if score:
            hits.append((score, table, column))
    hits.sort(key=lambda item: (-item[0], item[1]["name"], item[2]["name"]))
    return hits[:limit]


def describe_table(catalog, name: str):
    for table in catalog["tables"]:
        if table["name"] == name:
            return table
    return None
```

### 4.2 工具实现

```python
class SearchColumnsArgs(BaseModel):
    keyword: str = Field(description="业务关键词，如 'GMV'、'渠道'、'支付时间'")
    limit: int = Field(default=20, ge=1, le=50)


class SearchColumnsTool(Tool[SearchColumnsArgs]):
    def __init__(self, catalog):
        self._catalog = catalog

    @property
    def name(self) -> str:
        return "search_columns"

    @property
    def description(self) -> str:
        return (
            "按业务关键词查字段。用于：不确定字段名、不知道某指标该用哪些字段、"
            "需要在多张表之间找候选列。不要用它执行查询，也不要用它替代 describe_table。"
        )

    def get_args_schema(self):
        return SearchColumnsArgs

    async def execute(self, context, args):
        hits = search_columns(self._catalog, args.keyword, args.limit)
        if not hits:
            return ToolResult(
                success=True,
                result_for_llm=(
                    f"未找到与 '{args.keyword}' 相关的字段。"
                    "请换一个业务词，或先用 describe_table 查看核心表。"
                ),
            )
        lines = [
            f"{table['name']}.{column['name']}  {column['type']}  -- "
            f"{column.get('comment') or '无说明'}"
            for _, table, column in hits
        ]
        return ToolResult(success=True, result_for_llm="\n".join(lines))
```

`describe_table` 返回：

```text
表：shop.orders（36,000 行，MergeTree）
说明：订单主表，一单一行；金额单位为分
字段：
- order_id UInt64 — 订单唯一 ID
- status LowCardinality(String) — 订单状态；取值: paid/shipped/...
索引/分区：PARTITION BY toYYYYMM(created_at) ORDER BY (toDate(created_at), order_id)
```

### 4.3 DoD

- [ ] 搜索覆盖表名、字段名、注释、表说明
- [ ] 空结果给出可执行建议（换词/describe）
- [ ] `describe_table` 对未知表返回“表不存在 + 相近表名”
- [ ] 单测：大小写、别名（`gmv` 命中 `amount_cents` 注释）、limit 上限

---

## 5. T2-2：`sample_rows` / `explain_query`

### 5.1 `sample_rows`

参数：`table: Literal[...]`（由 catalog 动态生成不方便，Pydantic 里用 `str` + 白名单校验）、`n: int = 5`（1~20）。

安全要求：

- 表名必须在 catalog 中存在，否则拒绝
- 强制 `LIMIT n`，不接受 LLM 传入 SQL
- 脱敏：`email`、`properties` 中的邮箱/手机号；字符串截断到 80 字
- 超时 10s
- 返回列名 + 前 n 行 + “共 N 列 / 已脱敏”说明

```python
async def execute(self, context, args):
    table = describe_table(self._catalog, args.table)
    if table is None:
        return ToolResult(success=False, result_for_llm="表不存在，请先用 search_columns 确认表名。")
    sql = f"SELECT * FROM shop.{quote(args.table)} LIMIT {args.n}"
    frame, error = await self._execute(sql, timeout=10)
    ...
```

### 5.2 `explain_query`

- 参数：`sql: str`
- 仅允许 `SELECT` / `WITH`
- 执行 `EXPLAIN PLAN <sql>`（ClickHouse），返回前 40 行文本
- 附一行提示：“重点看 read_rows / partitions，不要据此判断结果正确性”

DoD：

- [ ] 非 SELECT / 多语句直接拒绝
- [ ] `EXPLAIN` 超时 10s
- [ ] 结果截断到 4,000 字符，并标注总行数

---

## 6. T2-3：`get_metric_definition`

数据源：`analytics_agent/context/glossary.yaml`（与 Stage 1 共用）。

```python
class GetMetricArgs(BaseModel):
    name: str = Field(description="指标名或别名，如 GMV、净收入、复购率")
```

返回：

```text
指标：净收入
定义：GMV − 已批准退款
公式：GMV − SUM(refunds.amount_cents)/100 WHERE refunds.status='approved'
易错点：公司级按退款发生月；品类级按订单 cohort，并按商品金额占比分摊
```

找不到时返回候选指标列表（按包含关系匹配），不要编造。

DoD：

- [ ] 9 个指标全部可查
- [ ] 支持别名（大小写、中文名）
- [ ] 未命中返回候选，不返回空字符串

---

## 7. T2-4：工具 description 重写

每个 description 必须包含四段：

1. **做什么**（一句话）
2. **什么时候用**（触发条件）
3. **什么时候不要用**（禁忌）
4. **和谁分工**（替代关系）

示例：

```text
search_columns：按业务词查字段。
用：不知道字段名、不确定某指标由哪些字段计算。
不要用：执行查询、确认取值分布（用 sample_rows）。
替代关系：先 search_columns 缩小范围，再 describe_table 看单表全貌。
```

```text
run_sql：执行一条只读 SQL。
用：已经确定表/字段/口径，需要拿结果。
不要用：猜字段（用 search_columns）、确认枚举（用 sample_rows）。
注意：只允许单条 SELECT/WITH；无 LIMIT 会被自动追加。
```

DoD：

- [ ] 5 个新工具 + `run_sql` / `visualize_data` 全部重写
- [ ] 人工读一遍，确认没有“两个工具都能做同一件事”的模糊地带

---

## 8. T2-5：参数收敛

- `sample_rows.n`：`Field(ge=1, le=20)`
- `search_columns.limit`：`Field(ge=1, le=50)`
- `describe_table.table`：字符串 + 白名单校验（Pydantic 动态枚举成本高，运行时校验更稳）
- `explain_query.sql`：长度 ≤ 4,000，单语句
- 未来若加 `time_range` / `granularity`，用 `Literal["day","week","month"]`，不要让模型自由填

DoD：单测覆盖越界参数，错误信息明确告诉模型合法范围。

---

## 9. T2-6：`run_sql` 护栏改造

### 9.1 AST 只读校验（`tools/guardrails.py`）

```python
import sqlglot
from sqlglot import exp


def is_read_only(sql: str) -> tuple[bool, str]:
    try:
        statements = sqlglot.parse(sql, read="clickhouse")
    except Exception as exc:
        return False, f"SQL 解析失败：{exc}"
    if len(statements) != 1:
        return False, "只允许单条语句"
    node = statements[0]
    if isinstance(node, (exp.Select, exp.Union, exp.Explain)):
        return True, ""
    return False, f"只允许 SELECT/WITH/EXPLAIN，收到 {type(node).__name__}"
```

要点：

- 用 AST 而不是关键字：字符串字面量里的 `'delete'`、注释里的 `DROP` 不再误杀
- `WITH ... INSERT` 会被解析成 `Insert`，正确拦截
- 解析失败按失败处理，不放过

### 9.2 LIMIT 注入

```python
def ensure_limit(sql: str, limit: int = 1000) -> str:
    # 仅对最外层没有 LIMIT 的 SELECT 包装；EXPLAIN 不处理
    ...
    return f"SELECT * FROM (\n{sql}\n) LIMIT {limit}"
```

注意：

- 聚合结果加 LIMIT 无害
- 含 `FORMAT` 的 SQL 直接拒绝（无法安全包装）
- 返回给 LLM 时注明“已自动追加 LIMIT 1000”

### 9.3 服务端超时

`ClickHouseRunner` 增加 `query_settings`：

```python
class ClickHouseRunner(SqlRunner):
    def __init__(..., query_settings=None):
        self.query_settings = query_settings or {}

    async def run_sql(self, args, context):
        ...
        result = client.query(args.sql, settings=self.query_settings)
```

工厂：

```python
ClickHouseRunner(..., query_settings={"max_execution_time": 30})
```

**不要依赖 `asyncio.wait_for`**：`BLOCKING=True` 时它打不断线程，必须让 ClickHouse 自己超时。

### 9.4 截断说明

结果超过 `max_result_chars` 时，回喂文本必须包含：

```text
（结果已截断：共 12,345 行 × 8 列，仅展示前 1,000 字符）
```

避免模型把截断结果当成全量。

### 9.5 绕过测试（必须全过）

| 输入 | 期望 |
|---|---|
| `SELECT 'delete' AS s` | 允许（字符串不是写操作） |
| `SELECT 1; DROP TABLE shop.orders` | 拒绝（多语句） |
| `WITH x AS (SELECT 1) INSERT INTO ...` | 拒绝（AST 是 Insert） |
| `/* DROP */ SELECT 1` | 允许 |
| `SELECT * FROM shop.orders` | 允许，并追加 LIMIT |
| `SELECT * FROM shop.orders FORMAT JSON` | 拒绝（无法安全 LIMIT） |

DoD：`tests/test_sql_guardrails.py` 覆盖以上 6 条 + 超长 SQL + 空 SQL。

---

## 10. T2-7：错误结构化回喂

把 `validate_sql` 的 `error_type` 与建议一起回喂：

```json
{
  "error_type": "read_only_violation",
  "message": "只允许 SELECT/WITH/EXPLAIN",
  "hint": "删除写操作，或改用 SELECT 查询；不要重试同一句 SQL"
}
```

执行错误（ClickHouse 异常）也做轻量分类：

- `UNKNOWN_IDENTIFIER` → “字段/别名不存在，先用 search_columns 确认”
- `SYNTAX_ERROR` → “检查函数名与括号，ClickHouse 不支持 DATE_TRUNC”
- `TIMEOUT` → “缩小时间范围或加分区过滤”
- 其它 → 原文 + “只改一次，不要原样重试”

DoD：单测断言错误文本包含 `error_type` 与 `hint`，且不会把原始堆栈整段回喂。

---

## 11. 接线与权限

```python
registry.register_local_tool(SearchColumnsTool(catalog), access_groups=["admin", "analyst"])
registry.register_local_tool(DescribeTableTool(catalog), access_groups=["admin", "analyst"])
registry.register_local_tool(SampleRowsTool(catalog, runner), access_groups=["admin", "analyst"])
registry.register_local_tool(ExplainQueryTool(runner), access_groups=["admin", "analyst"])
registry.register_local_tool(GetMetricDefinitionTool(glossary), access_groups=["admin", "analyst"])
```

- `viewer` / 空组仍然只能看到“无工具”提示（G 类用例不能回归）
- 不启用记忆工具（Stage 3 再开）

提示词只追加一段，不改 L0 主体：

```text
## 工具使用
- 不确定字段名 → search_columns；需要单表全貌 → describe_table。
- 需要确认枚举/日期格式 → sample_rows；大表先 explain_query。
- 口径不清 → get_metric_definition，不要凭记忆编公式。
- 已确定表和字段后，才调用 run_sql。
```

`PROMPT_VERSION` 升到 `stage2-v1`。

DoD：

- [ ] `build_agent` 默认注册 5 个新工具
- [ ] G-23/24/25 仍然 PASS（viewer 无工具）
- [ ] 报告 `agent.tool_names` 可追溯本次可用工具

---

## 12. 验证与 G2

### 12.1 跑法

```bash
python -m analytics_agent.evaluation.cli check --strict
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml
```

### 12.2 记录指标

| 指标 | Stage 1 | Stage 2 第 1 次 | Stage 2 第 2 次 |
|---|---|---|---|
| EX | 61.9% | | |
| behavior | 88.9% | | |
| 平均工具轮次 | 1.1~1.2 | | |
| P95 | 16.5s | | |
| `TOOL_MISUSE` | — | | |
| 复杂查询通过率 | 5/12 | | |

> 复杂查询定义：B/C/D 层中涉及 ≥3 表 join 或时间窗口的用例；先人工列出，再统计。

### 12.3 人工轨迹抽检（20 条）

重点看：

1. 有没有“先跑 SQL 报错，再回头找字段”的低效路径
2. 有没有该用 `sample_rows` 却直接猜枚举
3. 有没有重复调用同一工具、参数几乎不变
4. `run_sql` 的 LIMIT/超时是否真的生效
5. 工具返回的截断说明是否被模型正确理解

`TOOL_MISUSE` 计数规则：

- 用 `search_columns` 执行查询
- 用 `run_sql` 查 `system.*`
- 对同一问题重复 3 次以上相同工具调用
- 忽略工具返回的“表不存在/字段不存在”继续猜

---

## 13. 每日步骤（D1~D6）

### D1：catalog 工具（约 4 小时）

1. 实现 `context/search.py`（纯函数 + 单测）
2. 实现 `search_columns` / `describe_table`
3. 注册到 `build_agent`，手工调用一次

**DoD**：`search_columns("gmv")` 能命中 `orders.amount_cents` 注释；`describe_table("orders")` 返回 10 个字段。

### D2：sample / explain（约 4 小时）

1. 实现 `sample_rows`（白名单 + 脱敏 + LIMIT）
2. 实现 `explain_query`（只读 + 超时 + 截断）
3. 单测：不存在表、超长 n、非 SELECT

### D3：metric 与描述（约 3 小时）

1. 实现 `get_metric_definition`
2. 重写 7 个工具的 description
3. 更新 L0 工具使用段，版本升 `stage2-v1`

### D4：护栏（约 4 小时）

1. 引入 `sqlglot`，实现 `guardrails.py`
2. 改 `ClickHouseRunner` 支持 `query_settings`
3. 改 `RunSqlTool`：AST 校验、LIMIT 注入、截断说明
4. 跑绕过测试 6 条

### D5：错误结构与联调（约 3 小时）

1. 结构化 `validate_sql` 错误
2. 执行错误分类
3. 手工跑 5 条典型问题：正常查询、写操作、字段错、超时、大结果

### D6：评测与 G2（约 3 小时）

1. `check --strict` → 跑两次 30 条
2. 填第 12 节指标
3. 人工抽检 20 条轨迹
4. 勾 G2，记录结论

---

## 14. 常见坑

| 坑 | 现象 | 处理 |
|---|---|---|
| 用 `asyncio.wait_for` 做超时 | 线程打不断，超时后仍在跑 | 用 ClickHouse `max_execution_time` |
| LIMIT 注入破坏 `FORMAT` | 语法错误 | 含 `FORMAT` 直接拒绝 |
| 关键字黑名单 | 字符串里的 `delete` 被误杀 | AST 校验 |
| `sample_rows` 返回 PII | 邮箱/手机号泄露 | 正则脱敏 + 字符串截断 |
| 工具描述太泛 | 模型在 `search_columns` 和 `describe_table` 之间乱选 | 写清“先搜后看”的分工 |
| 工具太多 | 模型每轮都调一遍 | description 写“不要重复调用”；G2 监控平均轮次 |
| catalog 过期 | 字段注释与库不一致 | DDL 变更后 `schema_catalog refresh` |
| 截断无提示 | 模型把前 1000 字当全量 | 回喂文本必须带总行数/总列数 |

---

## 15. 执行日志（每完成一步追加）

| 日期 | 步骤 | 操作 | 结果 | 备注 |
|---|---|---|---|---|
| | | | | |

---

## 附录 A：catalog 查询示例

```python
catalog = load_catalog()
search_columns(catalog, "gmv", limit=5)
# [orders.amount_cents, orders.discount_cents, ...]

describe_table(catalog, "orders")
# {"name": "orders", "rows": 36000, "summary": "...", "columns": [...]}
```

## 附录 B：description 模板

```text
{tool_name}：{一句话职责}。
用：{2~3 个触发条件}。
不要用：{2~3 个禁忌}。
分工：{和哪个工具前后配合}。
```

## 附录 C：测试清单

- [ ] `search_columns` 命中表名/字段名/注释
- [ ] 空关键词 / 空结果
- [ ] `describe_table` 未知表
- [ ] `sample_rows` 白名单、脱敏、n 越界
- [ ] `explain_query` 非 SELECT、多语句
- [ ] `get_metric_definition` 9 个指标 + 未命中
- [ ] 护栏 6 条绕过用例
- [ ] `run_sql` 截断文本含总行数/列数
- [ ] `viewer` 仍无工具（G-23/24/25）
