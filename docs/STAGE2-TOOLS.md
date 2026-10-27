# Stage 2 执行手册：工具面重构与 SQL 护栏

> 本文是 `docs/AGENT_QUALITY_PLAN.md` 中 **Stage 2 / T2-1 ~ T2-7** 的详细执行版。
>
> - 状态：**T2-1 ~ T2-7 代码完成，待 G2 评测（2026-10-26）**
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
| 提示词 | 2,726 tokens（Stage 1 最终） |
| 两次差异 | 0pt |

### 0.2 剩余失败（Stage 2 的输入）

| 用例 | 现象 | 根因 |
|---|---|---|
| C-14 | 每周订单数 | 周窗口/边界仍未对齐 |
| C-15 | 净收入环比 | 模型漏扣退款 |
| C-16 | 12 个月趋势 | 首月环比 / 空月处理 |
| D-04 | 上周转化率 | 有时反问、有时比率/百分比混用 |
| D-17 | 7 日留存 | 留存窗口/观察期口径不一致（模型可能包含未满 7 天的 cohort） |
| D-18 | 加购→下单 | 模型算成“全部会话转化率” |
| E-05 | 复购率 | 仍然直接回答，不澄清 |
| I-30 | 30 天环比 | 返回多行对比，golden 是单值 |
| B-02 / C-03 / H-26 | 单条翻转 | 表达形式抖动，总体 EX 稳定 |

这些失败中，**只有一部分能靠工具解决**（探查字段、取值、扫描量）；口径类失败仍要靠 prompt 与 golden。Stage 2 不承诺把它们全部修掉。

### 0.3 Stage 1 已冻结、Stage 2 不改的东西

- 30 条用例与 golden（除发现真实错误）
- 评分规则、自然日窗口、数据锚点语义
- Stage 1 的 L0/L1/L2 结构（Stage 2 只追加工具使用规则，版本升到 `stage2-v2`，不重写主体）

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
- [x] `run_sql` 绕过测试 100% 拦截（写操作、多语句、超长、注释绕过、字符串字面量）
- [ ] `SCHEMA_MISSING` / `DIALECT_ERROR` = 0（待评测）
- [ ] 复杂查询（≥3 表 join）成功率 ≥ 60%（待评测）
- [ ] P95 ≤ **20s**（待评测）
- [x] 工具单测覆盖：catalog 搜索、脱敏、LIMIT 注入、超时透传、错误结构

> 统计提醒：21 条 answer 每条≈4.8pt；从 61.9% 到 70% 约 2 条用例。Stage 2 的真实价值更多在“护栏正确性”与“轨迹质量”，不要只盯 EX。
>
> 代码/单测项已满足；EX、behavior、P95、复杂查询等指标项需等 G2 评测。

---

## 2. 设计原则

1. **保留 L1 schema**：12 张表渲染约 **1,556 tokens**，完整提示词 **2,902 tokens**（预算 4,500），全量放进提示词仍然划算；工具用于“深挖”，不是替代。
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
│   ├── params.py              # ToolArgs + TABLE_PATTERN
│   ├── descriptions.py        # run_sql 四段式描述
│   ├── search_columns.py      # search_columns
│   ├── describe_table.py      # describe_table
│   ├── sample_rows.py         # sample_rows（白名单/脱敏/10s）
│   ├── explain_query.py       # explain_query（SELECT/WITH）
│   ├── metric_definition.py   # get_metric_definition
│   ├── run_sql.py             # AnalyticsRunSqlTool（AST + LIMIT）
│   ├── visualize.py           # AnalyticsVisualizeTool（只改描述）
│   └── guardrails.py          # prepare_sql / SqlGuardError
├── context/
│   └── search.py              # catalog 上的纯函数：search / describe
└── agent_factory.py           # 注册 7 个工具（access_groups）
src/vanna/tools/
├── sql_errors.py              # 错误分类 + JSON 回喂
├── sql_validation.py          # AST 优先、关键字兜底
└── run_sql.py                 # 截断补充总行数/列数
tests/
├── test_catalog_tools.py
├── test_sample_tools.py
├── test_metric_tool.py
├── test_tool_params.py
├── test_tool_descriptions.py
├── test_sql_guardrails.py
└── test_sql_errors.py
```

新增依赖：`sqlglot>=25` 已加入 `pyproject.toml` 主依赖。应用侧 `guardrails.prepare_sql` **不设回退**（解析失败直接拒绝）；框架侧 `_ast_read_only` 只对 `SHOW/EXPLAIN/Command` 回退到关键字逻辑。

---

## 4. T2-1：`search_columns` / `describe_table`

### 4.1 三个模块的职责与使用场景（先读这一节）

> 代码注释已写在 `context/search.py`、`tools/search_columns.py`、`tools/describe_table.py`；
> 本节记录“为什么这样拆”，修改前先看这里，避免把检索逻辑又写回工具里。

#### 4.1.1 `context/search.py`：检索引擎

它回答的是：**业务词在 catalog 里对应哪些字段？**

纯内存操作，不连数据库，原因是：

- 工具只负责“参数校验 + 输出格式”，检索逻辑单独可测；
- 可以用 fixture 单测，不依赖 ClickHouse；
- catalog 启动时已加载，搜索零延迟、可重复，不会因数据库抖动影响工具结果。

| 函数 | 作用 |
|---|---|
| `_terms` | 同义词展开：`gmv` → `amount_cents`、`销售额`、`成交额` |
| `search_columns` | 按表名/字段名/注释/表说明打分排序 |
| `find_table` | 精确查表，兼容 `orders` / `shop.orders`，大小写不敏感 |
| `suggest_tables` | 表名拼错时给候选（`oder` → `orders`） |

打分规则（只用于排序，不代表概率）：

| 命中位置 | 分值 |
|---|---|
| 表名精确 | +6 |
| 表名包含 | +3 |
| 字段名精确 | +4 |
| 字段名包含 | +2 |
| 字段注释包含 | +1 |
| 表说明包含 | +1 |

同一字段被多个同义词命中时取最高分，不累加；同分按表名、字段名排序，保证结果稳定。

维护约定：新增业务叫法时改 `_SYNONYMS`，**不要改打分公式**；本模块不生成 SQL、不访问数据库。

#### 4.1.2 `search_columns`：字段级入口

定位：**“我不知道字段叫什么”** 时用。

适用：

- 用户说 GMV，但表里只有 `amount_cents`；
- 用户说“渠道”，不确定是 `channel` 还是 `source`；
- 用户说“复购”，需要知道用 `user_id` + `order_id`。

不适用：

- 已经知道表、想看整张表结构 → `describe_table`；
- 想确认枚举/日期格式 → `sample_rows`（T2-2）；
- 想拿数据 → `run_sql`。

输出一行一列：

```text
orders.amount_cents  Int64  -- 订单金额（分），已扣 discount_cents；不含退款
```

一个关键行为：**搜不到时返回 `success=True`**。因为“没找到”不是工具执行失败，模型应换词或去 `describe_table`，而不是原样重试同一个调用。

#### 4.1.3 `describe_table`：表级入口

定位：**“我知道是哪张表，但不确定字段、枚举、规模”** 时用。

输出包含：

```text
表：shop.orders（36,000 行，MergeTree）
说明：订单主表，一单一行；金额单位为分
字段（10）：
- status LowCardinality(String) — ...；取值: paid/shipped/completed/...
```

- 行数：判断“要不要先加时间过滤 / LIMIT”；
- 枚举：避免猜 `status`、`currency`、`channel` 的取值；
- 注释：理解字段业务含义。

不适用：

- 不知道表名 → 先 `search_columns`；
- 想看真实数据格式 → `sample_rows`；
- 想拿查询结果 → `run_sql`。

一个关键行为：**未知表返回 `success=False`**，并附相近表名。这是参数错误，必须让模型换表名重试，不能让它把“表不存在”误当成“表是空的”。

#### 4.1.4 三者关系与调用链

```text
用户问题
  │
  ├─ 不知道字段名 → search_columns（字段级、广度搜索）
  │       └─ 找到候选表/字段
  ├─ 知道表名     → describe_table（表级、单点深挖）
  │       └─ 确认字段、枚举、行数
  └─ 都确定了     → run_sql（T2-6 加护栏）
```

`search_columns` 是“广度搜索”，`describe_table` 是“单点深挖”，`search.py` 是两者共用的底层引擎。
三个模块都只读 catalog，不执行 SQL、不查数据库。

### 4.2 catalog 查询函数（`context/search.py`）

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

### 4.3 工具实现

```python
# SearchColumnsArgs 继承 tools.params.ToolArgs（extra="forbid" + strip）
class SearchColumnsArgs(ToolArgs):
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

### 4.4 DoD

- [x] 搜索覆盖表名、字段名、注释、表说明（含业务同义词）
- [x] 空结果给出可执行建议（换词/describe）
- [x] `describe_table` 对未知表返回“表不存在 + 相近表名”
- [x] 单测：同义词、注释命中、limit、空关键词、未知表

---

## 5. T2-2：`sample_rows` / `explain_query`

### 5.1 两个工具的职责与使用场景（先读这一节）

> 与 T2-1 不同：`sample_rows` / `explain_query` **会真的连 ClickHouse**。
> 因此除了“查什么”，还必须约束“能查多少、查多久、返回什么”。

#### 5.1.1 `sample_rows`：看真实取值

定位：**“注释看完了，但不确定数据长什么样”** 时用。

适用：

- 确认枚举大小写（`PAID` vs `paid`）、日期格式（`2026-10-24` vs `20261024`）；
- 看 `properties` 这类 JSON 字段的真实键名；
- 判断是否存在脏数据（空字符串、异常值）。

不适用：

- 聚合/统计 → `run_sql`；
- 不知道表名 → `search_columns` / `describe_table`。

输出：

```text
表：shop.users（前 5 行，共 7 列；已脱敏）
user_id  email     country ...
1        [email]   CN
...
```

安全约束：

| 约束 | 实现 |
|---|---|
| 表名白名单 | 必须是 catalog 中存在的表，否则拒绝且不查库 |
| 行数上限 | `n: 1~20`，SQL 强制 `LIMIT n` |
| 超时 | 10s，走 ClickHouse `max_execution_time` |
| 脱敏 | 邮箱 → `[email]`，大陆手机号 → `[phone]` |
| 字符串截断 | 单值超过 80 字截断；整体结果超过 4,000 字符截断并说明 |

关键行为：未知表返回 `success=False`，并附相近表名——参数错误必须让模型换表名，不能让它把“表不存在”当成“表是空的”。

#### 5.1.2 `explain_query`：看执行计划

定位：**“SQL 写好了，但担心全表扫/超时”** 时用。

适用：

- 大表（events 100 万行）或多表 join 前，确认扫描行数与分区裁剪；
- `run_sql` 超时后，判断是否需要缩时间范围。

不适用：

- 获取业务结果 → `run_sql`；
- 非 `SELECT` / `WITH` → 直接拒绝。

输出：

```text
read_rows: 100000
partitions: 30/30
...
说明：重点看 read_rows / partitions；EXPLAIN 只反映执行计划，不代表结果一定正确。
```

安全约束：

| 约束 | 实现 |
|---|---|
| 语句类型 | 只允许单条 `SELECT` / `WITH`（复用 `validate_sql`） |
| 超时 | 10s 服务端超时 |
| 结果大小 | 4,000 字符，超出标注“已截断” |
| 错误 | 返回 `error_type` + 建议，不抛堆栈 |

#### 5.1.3 三者关系与调用链

```text
不知道字段/表
  → search_columns / describe_table（只读 catalog）
  → sample_rows（看真实取值，查库但限行/脱敏）
  → explain_query（大表前置检查，查库但不返回业务数据）
  → run_sql（正式结果）
```

- `describe_table` 回答“字段是什么”；
- `sample_rows` 回答“值长什么样”；
- `explain_query` 回答“这条 SQL 要扫多少”；
- `run_sql` 才回答“结果是多少”。

#### 5.1.4 为什么超时必须走服务端

`ClickHouseRunner.BLOCKING = True`，`asyncio.wait_for` 只能取消协程等待，**打不断正在执行的同步查询线程**。
因此 T2-2 给 `ClickHouseRunner` 增加：

```python
async def run_query(self, sql, settings=None):
    ...
    client.query(sql, settings=settings or self.query_settings)
```

- `sample_rows` / `explain_query` 每次传 `{"max_execution_time": 10}`；
- 后续 T2-6 给 `run_sql` 配 `{"max_execution_time": 30}`。

### 5.2 `sample_rows`

参数：`table: str`（Pydantic 里用 `str` + catalog 白名单校验）、`n: int = 5`（1~20）。

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

### 5.3 `explain_query`

- 参数：`sql: str`
- 仅允许单条 `SELECT` / `WITH`
- 执行 `EXPLAIN PLAN <sql>`（ClickHouse），返回文本
- 附一行提示：“重点看 read_rows / partitions，不要据此判断结果正确性”

### 5.4 DoD

- [x] `sample_rows` 表名白名单、`LIMIT 1~20`、10s 服务端超时
- [x] 邮箱 → `[email]`、手机号 → `[phone]`、单值 80 字截断、整体 4,000 字符
- [x] `explain_query` 只接受 SELECT/WITH；写操作与多语句直接拒绝
- [x] `explain_query` 10s 超时、结果 4,000 字符截断
- [x] `ClickHouseRunner.run_query(sql, settings=...)` 支持 `max_execution_time` 透传（单测覆盖）

---

## 6. T2-3：`get_metric_definition`

### 6.1 职责与使用场景（先读这一节）

#### 6.1.1 定位

`get_metric_definition` 回答的是：**这个指标到底怎么算？**

- 只读 `analytics_agent/context/glossary.yaml`，**不查数据库**；
- 返回四段：指标名、定义、公式、易错点；
- 与 L2 同源：L2 渲染的是 `short`（进提示词），工具返回的是完整 `formula` + `pitfalls`（按需查）。

适用：

- 用户说“GMV”“净收入”“复购率”，模型不确定口径；
- 容易混淆的指标：GMV vs 净收入、支付金额 vs 退款金额；
- 需要确认退款归属、时间窗口、是否排除测试。

不适用：

- 查字段名 → `search_columns`；
- 查表结构 → `describe_table`；
- 拿数据 → `run_sql`。

示例输出：

```text
指标：净收入
定义：GMV - approved 退款
公式：GMV - SUM(refunds.amount_cents)/100 WHERE refunds.status='approved'
易错点：公司级按退款发生月；品类级按订单 cohort，并按商品金额占比分摊
```

#### 6.1.2 匹配顺序

`find_metric` 按以下顺序查找，避免“猜错指标”：

1. **key / 中文名精确匹配**（大小写不敏感）：`gmv`、`GMV`、`净收入`；
2. **同义词表** `_METRIC_ALIASES`：`成交额` → GMV、`复购` → 复购率；
3. **查询包含 key/name**：`各渠道 GMV 是多少` → GMV；
4. 命中多个 → 返回 `None`，由工具列出候选，**不猜**。

未命中时：

```text
未找到指标 'gpm'。候选：GMV
请从候选里选一个；不要自行编造公式。
```

#### 6.1.3 与 L2 的关系

| | L2（提示词） | 工具 |
|---|---|---|
| 数据源 | `glossary.yaml` | 同一份 |
| 内容 | 只渲染 `short`（约 173 tokens） | 定义 + 完整 `formula` + `pitfalls` |
| 时机 | 每轮常驻 | 模型按需调用 |
| 作用 | 让模型“知道有这些指标” | 让模型“确认具体公式” |

维护约定：两边同源，改 `glossary.yaml` 即同时生效；不要在工具里另写一份公式。

### 6.2 实现

```python
# GetMetricDefinitionArgs 继承 tools.params.ToolArgs
class GetMetricDefinitionArgs(ToolArgs):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="指标名或别名，如 GMV、净收入、客单价、复购率、MRR",
    )
```

`context/glossary.py` 新增三个纯函数：

| 函数 | 作用 |
|---|---|
| `find_metric` | 按 key/name/别名/包含关系查找，返回 `(key, metric)` |
| `suggest_metrics` | 未命中时给候选（编辑距离 + 包含关系） |
| `list_metric_names` | 列出全部指标展示名 |

工具只负责格式化输出与候选提示，不查库、不生成 SQL。

### 6.3 DoD

- [x] 9 个指标全部可查
- [x] 支持 key、中文名、别名、大小写（`GMV` / `gmv` / `成交额`）
- [x] 支持“各渠道 GMV 是多少”这类包含式查询
- [x] 未命中返回候选，不返回空字符串、不编造公式
- [x] 输出包含定义、公式、易错点

---

## 7. T2-4：工具 description 重写

### 7.1 原则

每个 description 统一四段：

1. **做什么**（一句话）
2. **用**（触发条件）
3. **不要用**（禁忌，指向正确工具）
4. **分工**（前后关系）

工具路由主要靠 description，而不是靠参数名；写得越清楚，模型越不会“用 run_sql 猜字段”。

### 7.2 七个工具的描述要点

| 工具 | 用 | 不要用 | 分工 |
|---|---|---|---|
| `search_columns` | 不确定字段名、指标对应字段 | 看单表全貌、执行查询 | 先搜索缩小范围 |
| `describe_table` | 已知表，需要字段/枚举/行数 | 未知表名、看真实值 | search → describe |
| `sample_rows` | 确认枚举大小写/日期格式/JSON 键名 | 聚合统计、未知表 | 结构 → 真实值 → run_sql |
| `explain_query` | 大表/多表 join 前评估扫描量 | 拿业务结果、非 SELECT | explain → run_sql |
| `get_metric_definition` | 不确定 GMV/净收入/复购率口径 | 查字段、查表、执行查询 | 口径 → SQL → run_sql |
| `run_sql` | 表/字段/口径都确定，需要结果 | 猜字段、确认取值、查口径 | 最后一步 |
| `visualize_data` | 已有 run_sql 结果，需要图表 | 没有结果、想查数 | run_sql → visualize |

### 7.3 L0 新增“工具使用”段

```text
## 工具使用
- 不确定字段/指标对应字段 → search_columns。
- 已知表，需要字段/枚举/行数 → describe_table。
- 确认真实取值/日期格式/JSON 键名 → sample_rows。
- 大表或多表 join 前 → explain_query。
- 口径不清（GMV/净收入/复购率）→ get_metric_definition。
- 表、字段、口径都确定后 → run_sql；图表用 visualize_data（先有结果）。
```

同时保留“禁止”段：

```text
- 不要查询 system.*；不要 SELECT *；明细必须带 LIMIT。
```

L0 总长 **1,008 tokens**（Stage 2 起预算调整为 **1,100**；完整表述优先于省 200 tokens）；
7 个工具全部可见时，完整提示词 **2,902 tokens**（预算 4,500）。提示词版本升为 `stage2-v2`。

### 7.4 实现位置

- 5 个新工具：直接改各自 `description` property
- `run_sql`：`RunSqlTool(custom_tool_description=RUN_SQL_DESCRIPTION)`
- `visualize_data`：`AnalyticsVisualizeTool` 子类覆盖 description，不改上游实现

### 7.5 DoD

- [x] 5 个新工具 + `run_sql` / `visualize_data` 全部四段式
- [x] L0 增加工具使用段（1,008 tokens，≤ 1,100）
- [x] `PROMPT_VERSION = "stage2-v2"`
- [x] 单测校验 7 个工具描述都包含“用/不要用/分工”

---

## 8. T2-5：参数收敛（实际就是用Pydantic做工具参数校验）

### 8.1 目标

参数不是“越自由越好”。模型能填的范围越窄，工具调用越稳定：

- 数量类参数用 `ge` / `le` 明确上下界；
- 表名用格式校验 + catalog 白名单双层保护；
- 未知字段直接拒绝，避免模型把 `sql` 塞进 `sample_rows`；
- 字符串先 strip 再校验，`"   "` 不算合法输入。

### 8.2 参数表

| 工具 | 字段 | 约束 | 越界时模型看到 |
|---|---|---|---|
| `search_columns` | `keyword` | 1~50 字符 | `String should have at least 1 character` |
| | `limit` | 1~50 | `less than or equal to 50` |
| `describe_table` | `table` | 1~60 + 表名格式 | `String should match pattern` |
| `sample_rows` | `table` | 1~60 + 表名格式 | 同上 |
| | `n` | 1~20 | `less than or equal to 20` |
| `explain_query` | `sql` | 6~4,000 字符 + 单语句 | `String should have at least 6 characters` |
| `get_metric_definition` | `name` | 1~50 字符 | `String should have at least 1 character` |

表名格式：

```text
^[A-Za-z_][A-Za-z0-9_.]*$
```

允许 `orders`、`shop.orders`、`order_items`；拒绝空格、分号、反引号、`WHERE` 等注入型内容。
格式校验不等于白名单：真正的表是否存在，仍由 `find_table` 查 catalog。

### 8.3 公共基类

#### 8.3.1 基类只声明配置，字段在子类

```python
class ToolArgs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
```

`ToolArgs` **没有声明任何业务字段**，只声明了配置。实际字段都在各工具的参数模型类体里，用“类型注解 + `Field(...)`”声明：

```python
class SampleRowsArgs(ToolArgs):
    table: str = Field(
        min_length=1,
        max_length=60,
        pattern=TABLE_PATTERN,
        description="表名，可带库名，如 orders 或 shop.orders",
    )
    n: int = Field(default=5, ge=1, le=20, description="返回行数（1~20）")
```

`table: str = Field(...)` 就是“声明一个字段 `table`”；`n` 同理。

#### 8.3.2 各工具声明的字段

| 模型 | 文件 | 声明字段 |
|---|---|---|
| `SearchColumnsArgs` | `tools/search_columns.py` | `keyword`、`limit` |
| `DescribeTableArgs` | `tools/describe_table.py` | `table` |
| `SampleRowsArgs` | `tools/sample_rows.py` | `table`、`n` |
| `ExplainQueryArgs` | `tools/explain_query.py` | `sql` |
| `GetMetricDefinitionArgs` | `tools/metric_definition.py` | `name` |
| `RunSqlToolArgs` | 框架 `capabilities/sql_runner.py` | `sql` |
| `VisualizeDataArgs` | 框架 `tools/visualize_data.py` | `filename`、`title` |

#### 8.3.3 Pydantic 如何收集字段

Pydantic 元类在**创建类时**扫描类体里的类型注解，把字段收集进 `model_fields`：

```text
SampleRowsArgs: ['table', 'n']
DescribeTableArgs: ['table']
ToolArgs: []          ← 基类没有业务字段
```

`extra="forbid"` 的判断逻辑：

```text
传入的 key ∈ 当前模型（含继承）的 model_fields ？
  是 → 继续校验类型/范围
  否 → Extra inputs are not permitted
```

所以 `SampleRowsArgs(table="orders", sql="SELECT 1")` 中，`sql` 不在 `model_fields`，直接报 `extra_forbidden`。

两个容易误解的点：

- `model_config` **不是字段**，不参与 `model_fields`，也不出现在 JSON Schema 的 `properties`；
- 字段可以继承：若在 `ToolArgs` 声明 `request_id`，所有子类都会自动接受。

#### 8.3.4 `ConfigDict` 的两个配置

**`extra="forbid"`**：只接受声明过的字段，多传直接报错：

```python
SampleRowsArgs(table="orders", sql="SELECT 1")
# ValidationError: Extra inputs are not permitted (type=extra_forbidden)
```

**`str_strip_whitespace=True`**：字符串先 strip，再做长度/正则校验：

顺序很重要：如果先校验长度再 strip，`"   "` 会通过；Pydantic 不是这个顺序。

假设我们有这样一个模型：

```python
from pydantic import BaseModel, ConfigDict

class SearchColumnsArgs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    keyword: str
    limit: int = 5
```

**✅ 成功情况（自动去空格）：**

python

```python
# 传入带空格的字符串
args = SearchColumnsArgs(keyword="  gmv  ")
print(args.keyword) 
# 输出: "gmv" (空格被自动清理了)
```

**❌ 失败情况（多余字段被拦截）：**

```python
# LLM 幻觉多传了字段
try:
    args = SearchColumnsArgs(keyword="gmv", limitt=10) 
except Exception as e:
    print(e)
# 输出报错: 1 validation error for SearchColumnsArgs
# limitt
#   Extra inputs are not permitted [type=extra_forbidden, ...]
```

#### 8.3.5 `TABLE_PATTERN` 的含义

```python
TABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.]*$"
```

| 部分 | 含义 |
|---|---|
| `^` | 从字符串开头 |
| `[A-Za-z_]` | 第一个字符必须是字母或下划线 |
| `[A-Za-z0-9_.]*` | 后续可以是字母、数字、下划线、点 |
| `$` | 到字符串结尾 |

允许：

```text
orders
shop.orders
order_items
_tmp
```

拒绝：

```text
orders; DROP TABLE x     # 分号、空格
orders where 1=1         # 空格
`orders`                 # 反引号
orders-name              # 连字符
订单表                    # 中文
```

两个关键点：

1. `TABLE_PATTERN` 本身只是字符串常量，**不会自动生效**；必须在字段上显式引用：
   ```python
   table: str = Field(..., pattern=TABLE_PATTERN)
   ```
2. 它**只做格式校验，不检查表是否存在**。表是否存在仍由 `find_table()` 查 catalog：
   ```text
   格式合法 + catalog 存在  → 通过
   格式合法 + catalog 不存在 → “未找到表，相近的表：...”
   格式非法                 → Pydantic 拒绝，工具不会执行
   ```

#### 8.3.6 实际报错示例

```text
=== 正常：strip + 默认值 ===
OK: table='shop.orders' n=5

=== 非法表名：分号注入 ===
('table',) | String should match pattern '^[A-Za-z_][A-Za-z0-9_.]*$' | type: string_pattern_mismatch

=== 未知字段 ===
('sql',) | Extra inputs are not permitted | type: extra_forbidden

=== n 越界 ===
('n',) | Input should be less than or equal to 20 | type: less_than_equal

=== keyword 全空白 ===
('keyword',) | String should have at least 1 character | type: string_too_short
```

#### 8.3.7 声明之后发生什么

1. `model_json_schema()` 把字段与约束写进工具 schema，发给 LLM（告知）；
2. LLM 返回参数 dict；
3. `ToolRegistry` 用 `model_validate(dict)` 强制校验；
4. 通过后 `execute()` 收到已验证的 Pydantic 实例。

完整的权限 → 校验 → 执行顺序见 8.5。

### 8.4 未来参数约定

若后续增加 `time_range` / `granularity`：

```python
TimeRange = Literal["last_7d", "last_30d", "mtd", "last_month"]
Granularity = Literal["day", "week", "month"]
```

不要用自由字符串；枚举值同时写进参数 `description`，让模型知道合法取值。

### 8.5 校验链路（框架视角）

工具参数不是工具自己解析的，执行前框架会强制校验。完整链路：

```text
Tool.get_args_schema()
  → get_schema() 生成 JSON Schema（发给 LLM）
  → LLM 返回 tool_call.arguments（原始 dict）
  → ToolRegistry.execute_tool_call()
      1. 查工具
      2. 权限校验
      3. args_model.model_validate(arguments)
      4. transform_args（RLS）
      5. tool.execute(context, validated_args)
```

#### 8.5.1 LLM 侧：JSON Schema

```python
args_model = self.get_args_schema()
schema = args_model.model_json_schema()
```

框架会把字段类型、`ge/le`、`pattern`、必填项写给模型。这一步是“告知”，模型仍可能不遵守。

#### 8.5.2 执行侧：Pydantic 强制校验

```python
# src/vanna/core/registry.py
try:
    args_model = tool.get_args_schema()
    validated_args = args_model.model_validate(tool_call.arguments)
except Exception as e:
    msg = f"Invalid arguments: {str(e)}"
    return ToolResult(success=False, result_for_llm=msg, error=msg)
```

关键点：

- 校验失败**不抛异常**，返回 `success=False`，模型下一轮能看到错误；
- `execute()` 收到的是**已验证的 Pydantic 实例**，不是 dict；
- 权限校验在参数校验之前，未授权用户看不到参数错误细节。

#### 8.5.3 `ToolArgs` 的实际效果

| 配置 | 模型输入 | 结果 |
|---|---|---|
| `extra="forbid"` | `{"table":"orders","sql":"..."}` | `Extra inputs are not permitted` |
| `str_strip_whitespace` | `"  GMV  "` | 先变 `"GMV"`，再校验长度 |
| `ge=1, le=20` | `n=21` | `Input should be less than or equal to 20` |
| `pattern=TABLE_PATTERN` | `orders; DROP ...` | `String should match pattern` |

因此 `execute()` 里不需要再写 `if n > 20`。业务校验（表是否存在）仍留在工具内，因为它依赖 catalog，属于语义校验。

#### 8.5.4 参数校验 ≠ 内容校验

| 层 | 负责 | 例子 |
|---|---|---|
| Pydantic | 参数类型/范围/格式 | `n=21`、表名带分号 |
| 工具内部 | 参数内容是否可执行 | 表是否存在、SQL 是否只读 |

`run_sql(sql="DROP TABLE ...")` 能通过 Pydantic（类型确实是字符串），真正的只读校验由 T2-6 的 AST 护栏负责。

#### 8.5.5 测试位置

| 层面 | 位置 |
|---|---|
| JSON Schema 生成 | `src/vanna/core/tool/base.py` |
| 运行时 Pydantic 校验 | `src/vanna/core/registry.py` |
| 参数模型 | `analytics_agent/tools/*.py` |
| 参数约束测试 | `tests/test_tool_params.py` |

### 8.6 DoD

- [x] 所有工具 Args 继承 `ToolArgs`（`extra="forbid"` + strip）
- [x] 数量参数有明确上下界
- [x] 表名有格式校验，且仍保留 catalog 白名单
- [x] 单测覆盖越界、未知字段、空白字符串、注入型表名
- [x] 错误信息包含字段名与合法范围（Pydantic 默认）

---

## 9. T2-6：`run_sql` 护栏改造

### 9.0 职责与场景

`run_sql` 只负责**执行**，不负责探索：

- 用：表、字段、口径都已确定，需要拿结果；
- 不要用：猜字段（`search_columns`）、确认取值（`sample_rows`）、查口径（`get_metric_definition`）、看执行计划（`explain_query`）。

护栏的目标不是“限制模型”，而是让**越过权限/成本的调用在执行前失败**，并把原因回喂给模型。

### 9.1 两层 AST 校验

#### 9.1.1 应用侧：`tools/guardrails.py`

```python
def prepare_sql(sql: str, *, limit: int = 1000) -> tuple[str, str]:
    text = sql.strip().rstrip(";").strip()
    if _FORMAT_RE.search(text):
        raise SqlGuardError("unsupported_format", ...)

    statements = sqlglot.parse(text, read="clickhouse")
    if len(statements) != 1:
        raise SqlGuardError("multiple_statements", ...)

    node = statements[0]
    if not isinstance(node, (exp.Select, exp.Union)):
        raise SqlGuardError("read_only_violation", ...)

    if node.args.get("limit") is not None:
        return text, ""
    return f"SELECT * FROM (\n{text}\n) AS _guarded LIMIT {limit}", "已自动追加 LIMIT ..."
```

应用侧是 **fail-closed**：解析失败直接报 `parse_error`，不执行。

#### 9.1.2 框架侧：`src/vanna/tools/sql_validation.py`

框架的 `RunSqlTool` 内部还会调用 `validate_sql()`；如果只改应用侧，第二层仍会用关键字黑名单把 `'delete'` 误杀，所以框架侧也改为 AST 优先：

```python
def _ast_read_only(sql: str) -> Optional[bool]:
    statements = sqlglot.parse(sql, read="clickhouse")
    ...
    if isinstance(node, (exp.Select, exp.Union)):
        return True

    write_nodes = (Insert, Update, Delete, Drop, Create, Alter, ...)
    if isinstance(node, write_nodes):
        return False
    return None


def _is_read_only_query(sql: str) -> bool:
    ...
    ast_result = _ast_read_only(sql)
    if ast_result is not None:
        return ast_result

    # 无法判断（SHOW/EXPLAIN/Command 或解析失败）才回退关键字
    upper = cleaned.upper()
    return not any(re.search(rf"\b{kw}\b", upper) for kw in _WRITE_KEYWORDS)
```

三种返回值：

| AST 结果 | 含义 | 后续 |
|---|---|---|
| `True` | 明确是 `SELECT/WITH` | 直接放行，跳过关键字扫描 |
| `False` | 明确是 `Insert/Update/Delete/Drop/...` | 直接拒绝 |
| `None` | `SHOW/EXPLAIN/Command` 或解析失败 | 回退到原关键字逻辑 |

**为什么必须两层都改**：

```text
AnalyticsRunSqlTool.execute()
  ├─ 1. guardrails.prepare_sql()          ← 应用侧 AST
  └─ 2. super().execute()
          └─ RunSqlTool.validate_sql()
                └─ _is_read_only_query()   ← 框架侧关键字
```

只改第 1 层时，`SELECT 'delete' AS s` 会在第 2 层再次被拒绝。

边界：

- 应用侧：解析失败 **拒绝**（fail-closed）；
- 框架侧：解析失败 **回退关键字**（兼容 `SHOW/EXPLAIN`），不是唯一防线。

### 9.2 LIMIT 注入

无顶层 `LIMIT` 时包一层：

```text
SELECT * FROM (
  <原 SQL>
) AS _guarded LIMIT 1000
```

注意：

- 聚合结果加 LIMIT 无害；
- 含 `FORMAT` 的 SQL 直接拒绝（无法安全包装）；
- 返回给 LLM 时前置一行“已自动追加 LIMIT 1000（原 SQL 无 LIMIT）”；
- 已有 `LIMIT 10` 的 SQL 不重复包装。

### 9.3 服务端超时

`ClickHouseRunner` 增加 `run_query(sql, settings=...)`：

```python
async def run_query(self, sql, settings=None):
    ...
    result = client.query(
        sql,
        settings=self.query_settings if settings is None else settings,
    )
```

工厂：

```python
ClickHouseRunner(..., query_settings={"max_execution_time": 30})
```

**不要依赖 `asyncio.wait_for`**：`BLOCKING=True` 时它打不断线程，必须让 ClickHouse 自己超时。
`sample_rows` / `explain_query` 每次覆盖为 10s，`run_sql` 用默认 30s。

### 9.4 截断说明

结果超过 `max_result_chars` 时，回喂文本包含：

```text
(Results truncated to 1,000 characters: total 12,345 rows x 8 columns. ...)
```

保留原“Results truncated to N characters”前缀（兼容既有提示词/测试），补充总行数与列数，避免模型把截断结果当成全量。元数据同时增加 `results_truncated`。

### 9.5 绕过测试（全部通过）

| 输入 | 期望 | 结果 |
|---|---|---|
| `SELECT 'delete' AS s` | 允许（字符串不是写操作） | ✅ |
| `SELECT 1; DROP TABLE shop.orders` | 拒绝（多语句） | ✅ |
| `WITH x AS (SELECT 1) INSERT INTO ...` | 拒绝（AST 是 Insert） | ✅ |
| `/* DROP */ SELECT 1` | 允许 | ✅ |
| `SELECT * FROM shop.orders` | 允许，并追加 LIMIT | ✅ |
| `SELECT * FROM shop.orders FORMAT JSON` | 拒绝（无法安全 LIMIT） | ✅ |

### 9.6 实现与测试位置

| 层 | 位置 |
|---|---|
| 应用侧护栏 | `analytics_agent/tools/guardrails.py` |
| 应用侧工具 | `analytics_agent/tools/run_sql.py`（`AnalyticsRunSqlTool`） |
| 框架侧 AST | `src/vanna/tools/sql_validation.py` |
| 框架侧截断 | `src/vanna/tools/run_sql.py` |
| 绕过测试 | `tests/test_sql_guardrails.py`（10 项） |
| 框架回归 | `tests/test_run_sql_guardrails.py` |

### 9.7 DoD

- [x] `sqlglot` 加入依赖，AST 只读校验生效
- [x] 字符串字面量 `'delete'` 不再误杀；`WITH ... INSERT` 正确拦截
- [x] 无 LIMIT 自动追加，并在回喂文本中说明
- [x] `run_sql` 使用服务端 30s 超时；sample/explain 10s
- [x] 截断文本包含总行数/列数
- [x] 6 条绕过用例全部通过（10 项单测）
- [x] 框架侧同步改为 AST 优先，避免二次误杀

---

## 10. T2-7：错误结构化回喂

### 10.0 职责与原则

目标：让模型看到**可行动的错误**，而不是一长串异常。

- 统一输出 JSON：`error_type` + `message` + `hint`
- UI 仍显示人话，只有 `result_for_llm` 是 JSON
- 不把堆栈整段回喂，也不吞掉原始错误（`message` 保留关键信息）
- 不自动重试；由模型决定改 SQL 还是停止

### 10.1 统一格式

```json
{
  "error_type": "unknown_identifier",
  "message": "Error executing query: Code: 47. Unknown identifier: foo",
  "hint": "字段或别名不存在。先用 search_columns 确认字段名，再改 SQL；不要原样重试。"
}
```

实现：`src/vanna/tools/sql_errors.py`

```python
def error_payload(error_type, message, hint) -> str:
    return json.dumps(
        {"error_type": error_type, "message": message, "hint": hint},
        ensure_ascii=False,
        separators=(",", ":"),
    )
```

### 10.2 校验类错误

来源：`validate_sql` / `guardrails.prepare_sql`。

| `error_type` | hint |
|---|---|
| `empty_sql` | SQL 不能为空，请给出完整查询 |
| `sql_too_long` | SQL 过长，去掉重复 CTE/子查询 |
| `multiple_statements` | 只保留一条 SELECT/WITH，不要分号堆叠 |
| `read_only_violation` | 只允许 SELECT/WITH；删除写操作 |
| `unsupported_format` | 去掉 FORMAT 子句 |
| `parse_error` | 检查括号/引号/方言，不要原样重试 |
| `invalid_sql` | 按错误信息修改后重试一次 |

### 10.3 执行类错误

来源：ClickHouse 异常，`classify_sql_error()`。

| `error_type` | 触发关键词（示例） | hint |
|---|---|---|
| `timeout` | `TIMEOUT_EXCEEDED`、`max_execution_time`、`Code: 159` | 缩小时间范围 / 加分区过滤 / 先 explain_query |
| `unknown_identifier` | `Unknown identifier`、`Code: 47/207` | 先用 search_columns 确认字段 |
| `unknown_table` | `Unknown table`、`doesn't exist`、`Code: 60/81` | 先用 search_columns / describe_table 确认表名 |
| `syntax_error` | `Syntax error`、`Code: 62` | 检查函数名/括号/方言 |
| `memory_limit` | `Memory limit exceeded`、`Code: 241` | 减少扫描列/行，先聚合 |
| `division_by_zero` | `Division by zero`、`Code: 153` | 用 `nullIf(x, 0)` 包住分母 |
| `read_only_violation` | `readonly`、`Code: 164` | 当前连接只读，只允许 SELECT/WITH |
| `connection_error` | `Connection refused`、`network` | 环境问题，不要重复重试 |
| `sql_error` | 其它 | 读错误信息，只改一次，不要原样重试 |

### 10.4 应用位置

| 位置 | 改了什么 |
|---|---|
| `src/vanna/tools/run_sql.py` | 校验失败、超时、**执行异常** 三类都返回 JSON；补上了原先未捕获的执行异常 |
| `analytics_agent/tools/run_sql.py` | `SqlGuardError` 转 JSON，`error` 字段保留 `error_type` |
| `analytics_agent/tools/sample_rows.py` | 数据库异常分类后回喂，`error` 仍是 `sample_failed` |
| `analytics_agent/tools/explain_query.py` | 校验/执行异常分类后回喂 |

一个顺带修复：框架 `RunSqlTool` 原先只捕获超时，runner 抛出的 `Unknown identifier` 会直接冒泡；现在统一捕获并分类。

### 10.5 DoD

- [x] 校验错误返回 `error_type` + `hint`
- [x] 执行错误按 ClickHouse 错误码/关键词分类
- [x] `result_for_llm` 为 JSON，UI 仍显示人话
- [x] 不把堆栈整段回喂
- [x] 单测覆盖：守卫错误、字段不存在、校验空 SQL、sample 错误
- [ ] **暂不评测**（按用户要求，G2 评测另行安排）

---

## 11. 接线与权限

```python
registry.register_local_tool(
    AnalyticsRunSqlTool(
        sql_runner=runner,
        custom_tool_description=RUN_SQL_DESCRIPTION,
        auto_limit=1000,
    ),
    access_groups=["admin", "analyst"],
)
registry.register_local_tool(
    AnalyticsVisualizeTool(), access_groups=["admin", "analyst"]
)
registry.register_local_tool(
    SearchColumnsTool(catalog), access_groups=["admin", "analyst"]
)
registry.register_local_tool(
    DescribeTableTool(catalog), access_groups=["admin", "analyst"]
)
registry.register_local_tool(
    SampleRowsTool(catalog, runner), access_groups=["admin", "analyst"]
)
registry.register_local_tool(
    ExplainQueryTool(runner), access_groups=["admin", "analyst"]
)
registry.register_local_tool(
    GetMetricDefinitionTool(glossary), access_groups=["admin", "analyst"]
)
```

- `run_sql` 的 runner 构造时带 `query_settings={"max_execution_time": 30}`；
- `viewer` / 空组仍然只能看到“无工具”提示（G 类用例待 G2 回归）；
- 不启用记忆工具（Stage 3 再开）。

L0 已包含工具使用段（不是额外独立段落，预算仍受 1,100 限制）：

```text
## 工具使用
- 不确定字段名 / 指标对应字段 → search_columns。
- 已知表名，需要字段 / 枚举 / 行数 → describe_table。
- 需要确认真实取值 / 日期格式 / JSON 键名 → sample_rows。
- 大表或多表 join，先 explain_query 看扫描量。
- 口径不清（GMV / 净收入 / 复购率）→ get_metric_definition。
- 表、字段、口径都确定后，才调用 run_sql。
- 需要图表时用 visualize_data（必须先有 run_sql 结果）。
- 不要用 run_sql 查 system.*；不要用 search_columns 执行查询。
```

`PROMPT_VERSION` 升到 `stage2-v2`，L0 1,008 tokens，完整提示词 2,902 tokens。

DoD：

- [x] `build_agent` 默认注册 **7 个工具**（5 个新工具 + `run_sql` / `visualize_data`）
- [x] `run_sql` 使用服务端 30s 超时；sample/explain 10s
- [x] viewer / 空组无工具（注册层未给 viewer 分组；G-23/24/25 待 G2 评测回归）
- [x] 报告 `agent.tool_names` 可追溯本次注册的工具（全部注册工具，不区分用户分组）

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
> 当前状态：代码与单测完成，**尚未运行 G2 评测**。

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

> 状态：D1~D5 已完成（2026-10-26）；D6（G2 评测）按用户要求暂缓。

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
3. 更新 L0 工具使用段，版本升 `stage2-v2`

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
| 2026-10-26 | T2-1 | 实现 `context/search.py`（同义词 + 打分 + 表查找） | 完成 | `gmv` 命中 `orders.amount_cents` 等 4 列 |
| 2026-10-26 | T2-1 | 实现 `search_columns` / `describe_table` | 完成 | 单元测试 10 passed |
| 2026-10-26 | T2-1 | 注册到 `build_agent`（admin/analyst） | 完成 | 接线测试 22 passed；viewer 仍无工具 |
| 2026-10-26 | T2-2 | `ClickHouseRunner` 增加 `run_query(sql, settings)` | 完成 | `max_execution_time` 透传；单测覆盖 |
| 2026-10-26 | T2-2 | 实现 `sample_rows` | 完成 | 白名单、LIMIT、脱敏、80 字截断、10s 超时 |
| 2026-10-26 | T2-2 | 实现 `explain_query` | 完成 | 仅 SELECT/WITH、EXPLAIN PLAN、4,000 字符截断 |
| 2026-10-26 | T2-2 | 注册到 `build_agent` | 完成 | 24 passed；viewer 仍无工具 |
| 2026-10-26 | T2-3 | `context/glossary.py` 增加查找/候选函数 | 完成 | key/中文名/别名/包含式匹配 |
| 2026-10-26 | T2-3 | 实现 `get_metric_definition` | 完成 | 定义 + 公式 + 易错点；未命中给候选 |
| 2026-10-26 | T2-3 | 注册并验证 | 完成 | 17 passed；真实 glossary：净收入/复购率/gpm 均正常 |
| 2026-10-26 | T2-4 | 7 个工具 description 统一四段式 | 完成 | run_sql/visualize 通过自定义描述覆盖 |
| 2026-10-26 | T2-4 | L0 增加“工具使用”段 | 完成 | 完整表述 1,008 tokens |
| 2026-10-26 | T2-4 | 预算调整：L0 900 → 1,100 | 完成 | 完整提示词 2,902 tokens（总预算 4,500） |
| 2026-10-26 | T2-4 | `PROMPT_VERSION` 升 `stage2-v2` | 完成 | 15 passed（含 7 工具描述校验） |
| 2026-10-26 | T2-5 | 新增 `tools/params.py` 公共基类 | 完成 | `extra="forbid"` + strip；表名 pattern |
| 2026-10-26 | T2-5 | 5 个工具 Args 迁移到 `ToolArgs` | 完成 | 上界/格式/未知字段统一 |
| 2026-10-26 | T2-5 | 参数越界与注入测试 | 完成 | 31 passed（param 6 项） |
| 2026-10-26 | T2-5 | 记录参数校验链路（Schema → Pydantic → execute） | 完成 | 见 8.5；含代码路径与测试位置 |
| 2026-10-26 | T2-5 | 补充 8.3：字段声明、`model_fields`、`TABLE_PATTERN` 与报错示例 | 完成 | 基类只声明配置，字段在子类类体 |
| 2026-10-26 | T2-6 | 引入 `sqlglot`，实现 `guardrails.prepare_sql` | 完成 | AST 只读 + LIMIT 注入 + FORMAT 拒绝 |
| 2026-10-26 | T2-6 | `AnalyticsRunSqlTool` 接入；框架 `sql_validation` AST 优先 | 完成 | `'delete'` 字符串不再误杀；`WITH...INSERT` 拦截 |
| 2026-10-26 | T2-6 | 服务端 30s 超时 + 截断总行数/列数 | 完成 | 10 项护栏测试；框架回归 39 passed |
| 2026-10-26 | T2-6 | 文档补充两层 AST 校验（应用侧 + 框架侧） | 完成 | 见 9.1；含三态返回值与边界说明 |
| 2026-10-26 | T2-7 | 新增 `vanna/tools/sql_errors.py` | 完成 | 校验/执行错误分类 + JSON 回喂 |
| 2026-10-26 | T2-7 | 框架 `RunSqlTool` 补执行异常捕获 | 完成 | 原先只捕获超时，异常会冒泡 |
| 2026-10-26 | T2-7 | 应用工具统一结构化错误 | 完成 | run_sql / sample_rows / explain_query |
| 2026-10-26 | T2-7 | 单测 | 通过 | 49 passed；按用户要求**暂不评测** |
| 2026-10-26 | 审计 | 更新 §3 目录、§11 接线与 DoD | 完成 | 7 工具、sqlglot、`agent.tool_names` |
| 2026-10-26 | 审计 | 同步总计划 Stage 2 工具数/验收/门禁 | 完成 | 5 → 7 工具；G2 与执行手册一致 |
| 2026-10-27 | 数据 | 重灌至锚点 `2026-10-26` | 完成 | 12 表；`check --strict` 41/41 |
| 2026-10-27 | D-17 | 修正 7 日留存 golden | 完成 | 自然日 D+1~D+7；仅前 3 完整月；结果 42.86/45.02/43.16 |

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
