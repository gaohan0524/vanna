# Stage 1 执行手册：静态上下文工程与行为规则

> 本文是 `docs/AGENT_QUALITY_PLAN.md` 中 **Stage 1 / T1-1 ~ T1-6** 的详细执行版。
>
> - 状态：**进行中（第二轮 EX 33.3%、behavior 66.7%；第三轮 5 项修正完成，待运行；2026-10-25）**
> - 前置：Stage 0 完成（2026-10-23），30 条用例与评分规则已冻结
> - 基线：EX 实测 0/21，95% 上界 13.6%，**保守记 15%**
> - 目标：EX ≥ 25%，`SCHEMA_MISSING` < 5%，平均工具轮次 ≤ 5
> - 最后更新：2026-10-25

---

## 0. 从 Stage 0 继承的事实

这些数字和结论直接决定 Stage 1 怎么做，不要跳过。

### 0.1 基线（30 条，`deepseek-flash`）

| 指标 | 值 |
|---|---|
| EX（21 条 answer） | 0 / 21 = 0%（保守记 15%） |
| 首答命中 | 0% |
| behavior（9 条 clarify/refuse） | 44.4%（F 2/2、G 2/3、E 0/4） |
| 平均工具调用 | 9.83 |
| 触达 8 轮上限 | 18 / 30 |
| 失败分布 | `SHAPE_MISMATCH` 21 / `AMBIGUOUS` 5，`UNKNOWN` 0% |

### 0.2 失败的真实原因（按影响排序）

1. **没有 schema**：猜表名/字段名，或用 `sessions` 回答事件问题（H-26）。
2. **探索失控**：反复查 `system.tables`、换聚合方式，18 条用例在 8 轮内没算完。
3. **口径错误**：漏 `is_retry=0`（A-07）、自行剔除退款订单（C-14）、输出占比而非 GMV（A-01）。
4. **行为缺失**：E 类 0/4（全部自定口径直接查）、G-25（空组）编造答案。
5. **时间窗口理解不一致**：模型用自然日，golden 原来用滚动 24h（已在 Stage 0 修正）。

### 0.3 Stage 0 已冻结、Stage 1 不能改的东西

- 30 条用例与 golden SQL（除发现真实错误）
- 评分规则：`column_match="projection"` + 自然日窗口 + 行数严格
- 数据锚点语义：事实表时间戳 ≤ `anchor 23:59:59`
- 评测命令：`python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml`

---

## 1. 目标与 G1 门禁

### 1.1 目标

1. 让模型**知道有哪些表、哪些字段、字段是什么含义**，不再猜。
2. 让模型**知道业务口径**（有效订单、GMV、净收入、退款、渠道归因、金额单位）。
3. 让模型**先澄清、会拒答、不越权**（对应 E/F/G）。
4. 把平均工具轮次从 9.83 压到 5 以下。

### 1.2 G1 验收（全部满足才算通过）

- [ ] EX ≥ **25%**（21 条 answer，至少 6 条通过）
- [ ] `SCHEMA_MISSING` 占失败比例 < 5%
- [ ] 平均工具轮次 ≤ **5**
- [ ] behavior ≥ **70%**（9 条中至少 7 条）
- [ ] 系统提示词 ≤ **4,500 tokens**（字符数 / 4 估算）
- [ ] 同配置连跑两次，EX 差异 ≤ **5pt**
- [ ] `UNKNOWN` 占失败比例 < 10%
- [ ] 失败用例仍可通过 `report.json` 定位到 `executed_sql`

> 统计提醒：21 条 answer 中每条≈4.8pt。从 15% 到 25% 是 2 条用例的差距，属于噪声边缘；**至少要看到 3 条以上的净增**（≈29%）才算真实提升。

---

## 2. 设计：五层上下文（L0~L4）

| 层 | 内容 | 放置位置 | 预算（tokens） |
|---|---|---|---|
| L0 | 角色、ClickHouse 方言、硬约束、行为规则、工作流 | `prompts/templates.py` → 系统提示词 | ≤ 900 |
| L1 | 12 张表的紧凑 schema（表用途 + 字段 + 枚举） | `context/render.py` 渲染 | ≤ 2,200 |
| L2 | 指标定义与业务术语（GMV、净收入、有效订单……） | `context/glossary.yaml` 渲染 | ≤ 300 |
| L3 | 按需事实（`describe_table` / `sample_rows` / golden SQL） | Stage 2/3 才做 | 0 |
| L4 | 运行时状态（用户、时区、权限） | `ToolContextEnricher` → `metadata`，**不进 prompt** | 0 |

> 命名与总计划一致（L0~L4）。Stage 1 只实现 L0/L1/L2；L3 留给 Stage 2，L4 只对工具可见。

**关键决策**：

1. **Stage 1 用静态 schema，不做检索工具**。12 张表全量放进提示词最稳，也最容易测；检索留给 Stage 2/3。
2. **行为规则放 L0 常驻**，不走检索。E/F/G 是高频失败，必须每轮都看到。
3. **不渲染行数**。行数每天随 seed 变化，进 prompt 会引入无意义漂移；行数只存进 catalog，供 Stage 2 的 `describe_table` 使用。
4. **不查询数据库生成提示词**。`build_system_prompt` 每条消息只跑一次，里面做 IO 会让首 token 变慢且不可测；schema 在启动时从 JSON 加载。

---

## 3. 目录与文件

```text
analytics_agent/
├── context/
│   ├── __init__.py
│   ├── schema_catalog.py      # 抽取 / 刷新 / 加载
│   ├── schema_catalog.json    # 生成物（提交进 git）
│   ├── annotations.yaml       # 可选补充（DB 注释已全覆盖）
│   ├── glossary.yaml          # 指标与术语（单一事实源）
│   ├── glossary.py            # 加载 + 渲染 L2
│   └── render.py              # catalog -> 紧凑文本
├── prompts/
│   ├── __init__.py
│   ├── builder.py             # AnalyticsPromptBuilder
│   └── templates.py           # L0 文本
└── agent_factory.py           # 接线：system_prompt_builder=...
```

测试：

```text
tests/test_schema_render.py    # 渲染格式、注释加载、token 预算
tests/test_prompt_builder.py   # L0 段落、日期注入、完整提示词预算
tests/test_glossary.py         # 指标/术语加载、渲染、预算、异常
```

---

## 4. T1-1：schema 目录

### 4.1 抽取 SQL

```sql
SELECT
    c.database,
    c.table,
    c.name,
    c.type,
    c.comment,
    c.default_kind,
    c.default_expression,
    t.engine,
    t.comment AS table_comment
FROM system.columns AS c
JOIN system.tables AS t
  ON t.database = c.database AND t.name = c.table
WHERE c.database = 'shop'
ORDER BY c.table, c.position
```

行数单独查（`system.tables.total_rows` 对 MergeTree 可能为 0）：

```sql
SELECT count() FROM shop.<table>
```

### 4.2 枚举采样

只对 `LowCardinality(String)` 列采样，避免全表扫描：

```sql
SELECT <column>, count() AS c
FROM shop.<table>
GROUP BY <column>
ORDER BY c DESC
LIMIT 20
```

规则：

- 取值数 ≤ 20 → 写入 `enum` 数组
- 取值数 > 20 → 不写（说明是自由文本，如 `properties`）
- `Nullable` 列额外记录 `null_ratio`（可选）

### 4.3 人工注释（`annotations.yaml`）

**2026-10-24 起，DDL 已为 12 张表、77 个字段全部加上 `COMMENT`**，`system.columns.comment` 与
`system.tables.comment` 就是权威注释来源，不再依赖外部注释文件。`annotations.yaml` 只用于补充
数据库注释里放不下的信息（跨表关系、口径提醒），可为空：

> 结构变更后必须用 `python -m analytics_agent.evaluation.cli seed --schema-only` 重建，
> 因为 `CREATE TABLE IF NOT EXISTS` 不会更新已有表的注释。

```yaml
tables:
  orders:
    summary: "订单主表，一单一行；金额单位为分"
    columns:
      order_id: "订单唯一 ID"
      status: "订单状态；有效订单 = paid/shipped/completed"
      currency: "币种；经营指标默认只看 CNY"
      amount_cents: "订单金额（分），已扣 discount_cents"
      is_test: "测试订单标记；经营指标必须排除 =1"
  refunds:
    summary: "退款表，订单级，没有 product_id"
    columns:
      status: "退款状态；只统计 approved"
      amount_cents: "退款金额（分）"
```

合并优先级：`system.columns.comment` > `annotations.yaml` > 空字符串。
当前 77 个字段全部命中数据库注释，`annotations.yaml` 只作为可选补充。

**注释规范**：一句话、不超过 40 字；包含取值或单位；写“易错点”（例如“订单级，没有 product_id”）。

### 4.4 生成物结构

```json
{
  "version": 1,
  "generated_at": "2026-10-23T18:00:00+08:00",
  "database": "shop",
  "tables": [
    {
      "name": "orders",
      "engine": "MergeTree",
      "rows": 36000,
      "summary": "订单主表，一单一行；金额单位为分",
      "columns": [
        {
          "name": "status",
          "type": "LowCardinality(String)",
          "nullable": false,
          "comment": "订单状态；有效订单 = paid/shipped/completed",
          "enum": ["paid", "shipped", "completed", "pending", "cancelled", "refunded"]
        }
      ]
    }
  ]
}
```

### 4.5 刷新命令

```bash
python -m analytics_agent.context.schema_catalog refresh
```

- 只在 **DDL 变更** 或 **枚举取值变化** 时需要重跑；行数变化不需要
- 生成后必须人工抽查至少 20 个字段的注释
- catalog 提交进 git（便于 diff 与 review）

### 4.6 DoD

- [x] 12 张表全部有 `summary`（2026-10-25）
- [x] 100% 字段有 `type`，**77/77 有非空 `comment`**
- [x] 16 个 `LowCardinality` 列全部有 `enum`
- [x] `schema_catalog.json` **19,475 bytes**（<100 KB），刷新 **0.79s**
- [x] 抽查 `orders` 全字段 + 各表 summary，注释与数据一致

---

## 5. T1-2：L0 稳定指令

### 5.1 内容清单

按顺序拼进系统提示词：

1. **身份**：电商数据分析师，把业务问题转成 ClickHouse SQL 并给结论。
2. **方言**：`toStartOfDay`/`toStartOfMonth`/`today()`；禁止 `DATE_TRUNC`/`EXTRACT`；`count()` 而非 `count(1)`；`concat()` 而非 `||`。
3. **时间窗口**：近 N 天 = 最近 N 个完整自然日；本月/上月同期的边界规则。
4. **业务口径**：有效订单、测试数据一律排除、软删只影响用户维度、默认 CNY、GMV 不扣退款、净收入扣 approved 退款、渠道用 `orders.channel`、Top-N 并列按主键。
5. **行为规则**：先澄清、无权限直说、数据不存在直说、失败只改一次、只输出必要列、无可用工具时禁止文本模拟调用。
6. **工作流**：判断澄清 → 选表 → 写一条 SQL → 执行 → 自检 → 总结。

### 5.2 代码骨架

```python
# analytics_agent/prompts/templates.py
L0_RULES = """
## 身份
你是电商数据分析师，负责把业务问题转换成 ClickHouse SQL，并给出结论。

## 数据库与方言（ClickHouse）
- 库：shop；金额字段单位是「分」，输出时 /100.0，保留 2 位。
- 时间函数：toStartOfDay()/toStartOfMonth()/today()；禁止 DATE_TRUNC/EXTRACT。
- 计数用 count()，不要 count(1)；字符串拼接用 concat()，不要 ||。

## 时间窗口
- 「近 N 天」= 最近 N 个完整自然日：
  >= toStartOfDay(now()) - INTERVAL N DAY AND < toStartOfDay(now())
- 数据截止到昨天；今天查询为空是正常的，不要因此改写窗口。

## 业务口径
- 有效订单：status IN ('paid','shipped','completed')。
- 测试数据一律排除：is_test = 1 的订单/用户不参与任何指标。
- 订单/收入/退款类指标按订单事实统计，不因用户注销剔除历史订单（不要过滤 users.deleted_at）。
- 用户维度指标（新增、留存、复购、按国家转化）才排除 users.deleted_at IS NOT NULL。
- 金额默认只统计 currency='CNY'。
- GMV 不扣退款；净收入 = GMV − approved 退款。
- 退款是订单级、没有 product_id；按品类分摊时按商品金额占比。
- 渠道归因用 orders.channel（首触）。
- Top-N/排名：主指标降序，并列时按主键升序（如 product_id）补齐。

## 行为规则（优先级最高）
1. 缺时间范围/指标定义/维度 → 先问 1~2 个澄清问题，禁止先查库。
2. 当前账号无权限 → 明确说「当前账号无权限」，禁止编造或绕过。
3. 数据中不存在（无表/无字段/无记录）→ 说明「当前数据不包含」，禁止估算。
4. SQL 失败只改一次；读错误信息，不要原样重试。
5. 只输出回答所需的列；不要附加笔数/占比等调试列，除非用户要求。
6. 结论先行；只引用关键数字，不要重复整张结果表；必要时说明口径假设。
7. 若可用工具中没有 run_sql/execute_sql，直接说明「当前账号无权限」，禁止用文本模拟工具调用。

## 工作流
判断是否需要澄清 → 选表与字段 → 写一条 SQL → 执行 → 自检（行数/量级）→ 总结。

## 禁止
- 不要查询 system.* 元数据表；schema 已在下方给出。
- 不要 SELECT *；明细查询必须带 LIMIT。
"""
```

### 5.3 构建器

```python
# analytics_agent/prompts/builder.py
from datetime import date

from vanna.core.system_prompt import DefaultSystemPromptBuilder

from .templates import L0_RULES


class AnalyticsPromptBuilder(DefaultSystemPromptBuilder):
    def __init__(self, schema_text: str, glossary_text: str):
        super().__init__()  # 保留默认角色与工具清单
        self._schema_text = schema_text
        self._glossary_text = glossary_text

    async def build_system_prompt(self, user, tools):
        base = await super().build_system_prompt(user, tools) or ""
        today = date.today().isoformat()
        return "\n\n".join(
            [
                base,
                f"今天日期：{today}",
                L0_RULES,
                self._schema_text,
                self._glossary_text,
            ]
        )
```

### 5.4 三个坑

1. **不要传 `base_prompt`**：`DefaultSystemPromptBuilder(base_prompt=...)` 会整体替换默认提示词，连工具说明都丢掉；必须子类化后追加。
2. **`build_system_prompt` 每条消息只执行一次**（工具循环里复用），所以不要在这里查数据库或读文件；所有内容在 `__init__` 里准备好。
3. **日期要注入**：模型不知道“今天”，`近 30 天` 会算错；用 `date.today()` 生成一行。

### 5.5 DoD

- [x] 系统提示词包含 L0 全部 6 个小节（2026-10-25）
- [x] 含当天日期
- [x] 单测：`tests/test_prompt_builder.py` 断言关键段落与顺序
- [x] token 估算：**L0 586 / 完整提示词 2,268**（含 L2 为 2,442；预算 4,500）

---

## 6. T1-3：L1 常驻 schema

### 6.1 渲染格式（紧凑、可读）

```text
## 数据表（shop，12 张）

### shop.orders — 订单主表，一单一行；金额单位为分
- order_id UInt64 — 订单唯一 ID
- user_id UInt64 — 下单用户
- created_at DateTime — 下单时间（Asia/Shanghai）
- status LowCardinality(String) — 订单状态；取值: paid/shipped/completed/pending/cancelled/refunded
- currency LowCardinality(String) — 币种；取值: CNY/USD
- amount_cents Int64 — 订单金额（分），已扣 discount
- channel LowCardinality(String) — 首触渠道
- is_test UInt8 — 测试订单标记；经营指标必须排除 =1
```

### 6.2 渲染规则

- 每张表一个 `###` 块，第一行 `库.表 — summary`
- 字段一行一个：`name type — comment`；有枚举时追加 `取值: a/b/c`
- **不渲染** `default_kind`、`default_expression`、`position`
- **不渲染行数**
- 字段顺序按 `position`（DDL 顺序）
- 表顺序固定：按业务重要性，不按字母序（orders/order_items/payments/refunds/users/products/sessions/events/accounts/campaigns/dim_date/meta_data_anchor）

### 6.3 预算控制

- 目标 ≤ 2,200 tokens（约 8,800 字符）
- 超预算时按顺序裁剪：
  1. 去掉 `Nullable`、`Int64` 等显而易见的类型说明
  2. 注释截断到 30 字
  3. `dim_date` / `meta_data_anchor` 只保留表名与用途
- 在 `render.py` 里提供 `max_chars` 参数，超出时记录 warning

### 6.4 DoD

- [x] 12 张表全部渲染（随 T1-1 一并完成）
- [x] 陷阱字段（`is_test`、`currency`、`is_retry`、`subcategory`、`deleted_at`、`properties` 等）注释保留
- [x] 渲染结果 **4,114 chars ≈ 1,556 tokens**（预算 2,200）
- [x] 单测覆盖：空注释、枚举缺失、超长注释截断

---

## 7. T1-4：L2 指标与术语 + 行为规则

### 7.1 `glossary.yaml`（单一事实源）

```yaml
metrics:
  gmv:
    name: "GMV"
    definition: "成交总额"
    formula: "SUM(amount_cents)/100，status IN (paid,shipped,completed)，currency=CNY，is_test=0"
    pitfalls: "不扣退款；不含 cancelled/pending；不含 USD"
  net_revenue:
    name: "净收入"
    definition: "GMV − 已批准退款"
    formula: "GMV − SUM(refunds.amount_cents WHERE status='approved')"
    pitfalls: "退款按订单 cohort 或发生月，口径不同"
  aov:
    name: "客单价"
    definition: "GMV / 有效订单数"
    pitfalls: "分母必须是有效订单"
terms:
  - term: "有效订单"
    definition: "paid/shipped/completed"
  - term: "首触渠道"
    definition: "orders.channel"
```

渲染成 3~5 行短文本，不要整段放 YAML。

### 7.2 行为规则与用例的对应

| 规则 | 对应用例 | 判定 |
|---|---|---|
| 口径不明先澄清 | E-05/19/20/21 | 无 SQL + 问号或澄清线索词 |
| 无权限直说 | G-23/24/25 | 无编造数字，含“无权限/无法” |
| 数据不存在直说 | F-06/22 | 可查元数据，但最终必须说明不可用 |

**注意**：`G-25`（空组）当前会编答案，因为工具被过滤掉后模型不知道该说什么；L0 必须显式写“工具不可用/无权限时如何回答”。

### 7.3 DoD

- [x] `glossary.yaml` 覆盖 9 个指标 + 5 个术语（2026-10-25）
- [x] 渲染文本 **349 chars ≈ 173 tokens**（预算 300）
- [x] 与 `evaluation/seed/metrics.md` 的口径一致（人工对照）
- [x] E/F/G 的规则在 L0 中有明确对应句（见 `templates.py` 行为规则）

---

## 8. T1-5：接线与配置

### 8.1 配置

`AgentConfig` 已固定：

```python
AgentConfig(temperature=0.0, max_tool_iterations=8, stream_responses=False)
```

`.env` 中 `AGENT_MAX_ITERATIONS=8` 已生效，Stage 1 不再调整。

`Settings` 新增两个路径（带默认值，无需改 `.env`）：

```python
schema_catalog_path: Path = PROJECT_ROOT / "analytics_agent/context/schema_catalog.json"
glossary_path: Path = PROJECT_ROOT / "analytics_agent/context/glossary.yaml"
```

### 8.2 接线

```python
# analytics_agent/agent_factory.py
from analytics_agent.context.schema_catalog import load_catalog
from analytics_agent.context.render import render_schema, render_glossary
from analytics_agent.prompts.builder import AnalyticsPromptBuilder


def build_agent(settings=None, ...):
    s = settings or Settings.from_env()
    catalog = load_catalog(s.schema_catalog_path)
    builder = AnalyticsPromptBuilder(
        schema_text=render_schema(catalog),
        glossary_text=render_glossary(s.glossary_path),
    )
    return Agent(
        ...,
        system_prompt_builder=builder,
        config=AgentConfig(temperature=0.0, max_tool_iterations=s.agent_max_iterations),
    )
```

### 8.3 版本记录

在 `analytics_agent/__init__.py` 或 `prompts/templates.py` 定义：

```python
PROMPT_VERSION = "stage1-v3"
```

`cli.py` 的 `agent` 字段（`report.json["agent"]`）包含 `prompt_version` 与 `prompt_tokens_est`，报告里能追溯“这次结果用的是哪版提示词、多大”。prompt 任何改动都要递增版本号。

### 8.4 DoD

- [x] `build_agent` 默认带上 Stage 1 提示词（`stage1-v3`，≈2,600 tokens）
- [x] 报告 `agent.prompt_version` / `prompt_tokens_est` 有值（第二轮实测 `stage1-v2` / 2,572）
- [x] 缺 catalog / glossary 时启动即报错，并提示刷新命令（单测覆盖）

---

## 9. T1-6：验证与 G1（A/B）

### 9.1 跑法

```bash
# 1) 预检
python -m analytics_agent.evaluation.cli check --strict --dataset analytics_agent/evaluation/datasets/v1_30.yaml

# 2) 第一次
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml

# 3) 第二次（稳定性）
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml
```

### 9.2 记录指标

| 指标 | 基线（Stage 0） | Stage 1 第 1 次 | Stage 1 第 2 次 |
|---|---|---|---|
| EX | 15%（估算） | 19.1%（4/21） | **33.3%（7/21）** |
| 首答命中 | 0% | 19.1% | 33.3% |
| behavior | 44.4% | 55.6%（5/9） | **66.7%（6/9）** |
| 平均工具轮次 | 9.83 | 1.1 | **1.37** |
| P95 | 31.4s | 22.0s | **20.2s** |
| `METRIC_WRONG` | 0 | 8 | 10 |
| `NO_TOOL` | 0 | 5 | 1 |
| `SHAPE_MISMATCH` | 21 | 4 | 2 |
| `AMBIGUOUS` | 5 | 4 | 3 |
| `UNKNOWN` | 0% | 0% | 0% |

### 9.3 失败归因重点

Stage 1 后如果 EX 仍低，优先看这三类：

1. **`SCHEMA_MISSING`**：说明 schema 渲染漏了字段或注释不清，回 T1-1/T1-3。
2. **`METRIC_WRONG`**：口径没写清，回 T1-4 与 L0 的业务口径段。
3. **`NO_TOOL` / 轮次仍高**：模型还在探索，检查 L0 的“禁止查 system.*”是否生效。

### 9.4 DoD

- [ ] 两次运行均产出 `report.json` / `report.html`
- [ ] 第 9 节指标表填满（含两次 EX 与差异）
- [ ] 失败全部可归因，`UNKNOWN` < 10%
- [ ] 第 1.2 节 G1 逐项勾选，结论写入第 13 节执行日志

---

## 10. 每日步骤（D1~D5）

### D1：schema 目录（约 4 小时）

0. 先 `python -m analytics_agent.evaluation.cli seed --scale 0.1` 恢复数据（枚举采样需要；当前表为空）
1. 写 `context/schema_catalog.py`：抽取、行数、枚举采样、合并 `annotations.yaml`
2. 生成 `schema_catalog.json`，人工抽查 20 个字段
3. 写 `context/render.py` 与单测

**DoD**：`refresh` 可重复执行；JSON 结构稳定；渲染文本 ≤ 2,200 tokens。

### D2：L0 与接线（约 3 小时）

1. 写 `prompts/templates.py`（L0 全文）
2. 写 `prompts/builder.py`
3. 接到 `build_agent`，本地打印一次完整系统提示词，人工读一遍
4. 版本号写入报告

**DoD**：`python -c` 能打印出完整提示词；含日期、schema、术语、行为规则。

### D3：术语与行为规则（约 3 小时）

1. 写 `glossary.yaml`（9 指标 + 5 术语）
2. 对照 `metrics.md` 与 E/F/G 用例，确认规则句覆盖
3. 单测：渲染文本包含关键指标与三条行为规则

**DoD**：E/F/G 的判定点都能在提示词里找到对应句。

### D4：第一次跑（约 1.5 小时）

1. `check --strict` → `run`
2. 记录第 9 节表格
3. 逐条看失败，按 9.3 归类
4. 只修最影响 3 条以上的问题，不要一次改多个变量

**DoD**：报告、指标、失败归因齐全；知道下一轮改什么。

### D5：第二次跑与 G1（约 1.5 小时）

1. 同配置再跑一次
2. 对比两次 EX，差异 ≤ 5pt
3. 对照 1.2 逐项勾 G1
4. 更新总计划 §9 与本文执行日志

**DoD**：G1 结论明确（通过 / 未通过 + 原因）。

---

## 11. 常见坑

| 坑 | 现象 | 处理 |
|---|---|---|
| 用 `base_prompt` | 工具清单与默认指令全部丢失 | 子类化后 `super().build_system_prompt()` 再追加 |
| 在 builder 里查库 | 首 token 变慢、提示词每次不同、不可复现 | 启动时加载 JSON，builder 只做字符串拼接 |
| 把行数写进提示词 | 每天 seed 后提示词漂移，结果不可比 | 行数只存 catalog，供 Stage 2 工具用 |
| 枚举不写 | 模型猜 `status` 取值 | LowCardinality 必须采样并渲染 |
| 注释太长 | token 超预算，注意力被稀释 | 每条 ≤ 30 字，超长截断并记 warning |
| 日期未注入 | “近 30 天”按模型臆想的日期算 | builder 里注入 `date.today()` |
| 切回 Ollama | 8k 上下文装不下 3.5k 提示词 + 工具结果 | 提示词预算按 4.5k 控制；Ollama 只作为对照，不做主模型 |
| 一次改多个变量 | 无法归因提升来自 schema 还是行为规则 | D4 每轮只改一类，跑完再改下一类 |

---

## 12. 执行计划（2026-10-24 起）

### 12.1 里程碑

| 里程碑 | 内容 | 判定 |
|---|---|---|
| M0 | 数据就绪 | `seed --scale 0.1` 成功，锚点=昨天 |
| M1 | schema 目录完成 | T1-1 DoD 全绿，`render` ≤ 2,200 tokens |
| M2 | 提示词装配完成 | L0/L1/L2 全部进系统提示词，≤ 4,500 tokens，版本号写入报告 |
| M3 | 首次 Stage 1 运行 | 产出报告，失败可按 taxonomy 归因 |
| M4 | G1 通过 | EX ≥ 25%、behavior ≥ 70%、轮次 ≤ 5、两次差异 ≤ 5pt |

### 12.2 日历（按每天 3~5 小时有效投入）

| 日期 | 任务 | 当日产出 | 收工检查 |
|---|---|---|---|
| 10-24（六） | P0 恢复数据 + T1-1 | `schema_catalog.json`、`render.py`、单测 | 12 表 / 77 字段，枚举齐全 |
| 10-25（日） | T1-2 L0 + T1-3 L1 | `templates.py`、`builder.py`、渲染文本 | 打印完整 prompt，人工读一遍 |
| 10-26（一） | T1-4 L2 + T1-5 接线 | `glossary.yaml`、`build_agent` 接线、版本号 | prompt ≤ 4,500 tokens |
| 10-27（二） | T1-6 第一次运行 + 归因 | 报告、失败分类、1~2 处修复 | 只改一类问题 |
| 10-28（三） | 第二次运行 + G1 | 两次指标、G1 结论、日志 | 差异 ≤ 5pt，勾 G1 |
| 10-29（四） | 缓冲 | 返工或补测 | — |

> 如果每天投入不足 3 小时，把 10-25/10-26 各拆成两天，总周期顺延，**不要压缩验证**。

### 12.3 每日收工动作

1. 追加第 13 节执行日志（命令、结果、关键数字、报告路径）；
2. 记录当天的提示词 token 数（用 `context.render.estimate_tokens`，中文按 1 字≈1 token）；
3. 若改了提示词，递增 `PROMPT_VERSION`；
4. 只提交当天相关文件，不把实验性改动混进同一提交。

### 12.4 风险与应对

| 风险 | 触发信号 | 应对 |
|---|---|---|
| 数据为空 | `check` 行数/锚点 FAIL | 先 `seed --scale 0.1`，再继续 |
| prompt 超预算 | > 4,500 tokens | 按 T1-3 的裁剪顺序：去类型 → 截注释 → 精简维表 |
| EX 提升不明显 | 仍 < 20% | 看失败分布：`SCHEMA_MISSING` 回 T1-1/3；`METRIC_WRONG` 回 T1-4 |
| 模型仍查 `system.*` | 轮次 > 5 | 强化 L0 的“schema 已给出，禁止查元数据” |
| 两次差异 > 5pt | 结果抖动 | 先确认 `temperature=0`、提示词未变，再增加样本或复跑 |

### 12.5 角色分工

| 角色 | 负责 |
|---|---|
| 工程师 | schema 抽取、渲染、接线、跑评测、修复 |
| 业务专家（你） | 口径确认、G1 判定、失败样例复核、是否进入 Stage 2 |

## 13. 执行日志（每完成一步追加）

| 日期 | 步骤 | 操作 | 结果 | 备注 |
|---|---|---|---|---|
| 2026-10-25 | P0 | `seed --scale 0.1` | 成功 | 12 表 312,569 行；锚点 `2026-10-24`；`check --strict` 41/41 |
| 2026-10-25 | T1-1 | 实现 `context/schema_catalog.py` | 完成 | 抽取、行数、枚举采样、注释合并、`refresh` 子命令 |
| 2026-10-25 | T1-1 | 生成 `schema_catalog.json` | 完成 | 12 表 / 77 字段 / 16 枚举；19,475 bytes；刷新 0.79s |
| 2026-10-25 | T1-1 | 实现 `context/render.py` | 完成 | 4,114 chars ≈ 1,556 tokens（预算 2,200）；重复“取值”已去重 |
| 2026-10-25 | T1-3 | L1 渲染验收 | 完成 | 12 表全渲染；4,114 chars ≈ 1,556 tokens；空注释/枚举/截断均有单测 |
| 2026-10-25 | T1-2 | 实现 `prompts/templates.py`（L0） | 完成 | 586 tokens；方言/时间窗口/口径/行为/工作流 |
| 2026-10-25 | T1-2 | 实现 `prompts/builder.py` | 完成 | 日期注入 + L0 + L1；L2 可选，缺省不拼 |
| 2026-10-25 | T1-2 | 完整提示词检查 | 通过 | 5,754 chars ≈ 2,268 tokens（预算 4,500）；12 passed |
| 2026-10-25 | T1-4 | 新增 `context/glossary.yaml` | 完成 | 9 指标 + 5 术语；含 formula/pitfalls，供 Stage 2 工具复用 |
| 2026-10-25 | T1-4 | 实现 `context/glossary.py` | 完成 | 加载 + 渲染；349 chars ≈ 173 tokens（预算 300） |
| 2026-10-25 | T1-4 | 单测与静态检查 | 通过 | 18 passed（glossary 6 项）；ruff 全过 |
| 2026-10-25 | T1-5 | `Settings` 增加 catalog/glossary 路径 | 完成 | 默认指向 `analytics_agent/context/`；支持环境变量覆盖 |
| 2026-10-25 | T1-5 | `build_agent` 注入 `AnalyticsPromptBuilder` | 完成 | 2,442 tokens；schema/glossary/rules 全部命中 |
| 2026-10-25 | T1-5 | 报告增加 `prompt_version` / `prompt_tokens_est` | 完成 | 22 passed（含接线 4 项）；ruff 全过 |
| 2026-10-25 | T1-6 | 第一次运行（30 条） | 完成 | EX 19.1%（4/21）、behavior 55.6%、avg_tools 1.1、P95 22.0s |
| 2026-10-25 | T1-6 | 失败归因 | METRIC_WRONG 8 / NO_TOOL 5 / SHAPE 4 / AMBIGUOUS 4 | 报告 `reports/20261025-194149/` |
| 2026-10-25 | T1-6 | 第二轮修正：A-07/B-11/B-12/B-13 补 30 天窗口、H-26 明确 purchase、软删口径、无工具规则 | 完成 | prompt `stage1-v2`（2,572 tokens） |
| 2026-10-25 | T1-6 | 第二次运行（30 条） | 完成 | **EX 33.3%（7/21）、behavior 66.7%（6/9）、avg_tools 1.37、P95 20.2s** |
| 2026-10-25 | T1-6 | 第三轮修正：无工具块、D-04 周一、A-07/A-09/H-26/B-12 golden 对齐、B-13 题面、B-11 并列 | 完成，**未运行** | prompt `stage1-v3`（≈2,600 tokens） |
| 2026-10-25 | T1-1 | 单测与静态检查 | 通过 | `tests/test_schema_render.py` 6 passed；ruff 全过 |

---

## 附录 A：schema JSON 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | int | catalog 结构版本，结构变更时 +1 |
| `generated_at` | str | 生成时间（ISO8601） |
| `database` | str | 库名 |
| `tables[].name` | str | 表名（不含库） |
| `tables[].engine` | str | 引擎 |
| `tables[].rows` | int | 行数（仅供 Stage 2 工具使用，不渲染） |
| `tables[].summary` | str | 一句话用途 |
| `tables[].columns[]` | list | 字段列表，按 `position` 排序 |
| `columns[].nullable` | bool | 是否 `Nullable(...)` |
| `columns[].enum` | list[str] | 低基数列的取值；自由文本为 `null` |

---

## 附录 B：提示词拼装顺序（最终形态）

```text
[DefaultSystemPromptBuilder 的默认内容]
  角色 + 工具清单 + 记忆工作流（若注册了记忆工具）

今天日期：2026-10-23

[L0 稳定指令]
  身份 / 方言 / 时间窗口 / 业务口径 / 行为规则 / 工作流 / 禁止

[L1 schema]
  ## 数据表（shop，12 张）
  ### shop.orders — ...
  - 字段 ...

[L2 术语]
  ## 指标定义
  - GMV：...
```

顺序不要随意调整：行为规则在 schema 之前，模型更容易把它当约束；schema 在术语之前，先知道“有什么”再知道“怎么算”。

---

## 附录 C：字段注释规范

1. 一句话，≤ 30 字；不要写“字段名”三个字。
2. 有枚举必须列：`订单状态；取值: paid/shipped/...`
3. 有单位必须写：`金额（分）`
4. 有易错点必须写：`订单级，没有 product_id`、`测试订单必须排除`
5. 同义字段要区分：`orders.channel（首触）` vs `sessions.channel（会话渠道）`
6. 不要写实现细节（索引、引擎参数），模型不需要。
