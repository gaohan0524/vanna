# Stage 0 执行手册：评测基线与 ClickHouse 合成业务库

> 本文是 `docs/AGENT_QUALITY_PLAN.md` 中 **Stage 0 / T0-0 ~ T0-6** 的详细执行版。
> 完成本文全部验收项（G0）后，才进入 Stage 1（静态上下文工程）。
>
> - 状态：**D1 完成（灌数 + 预检 20/20），待冒烟 run 与首次基线**（见 0.1）
> - 主评测库：自建 ClickHouse 合成业务库（`shop`）
> - 方法论参考：`ClickHouse/data-agent-mnist`（Apache-2.0）
> - 业务专家：项目负责人本人（负责口径与 golden SQL 审核）
> - 最后更新：2026-10-22

---

## 0.1 实施状态（2026-10-22）

### 已完成

| 任务 | 状态 | 产物 / 证据 |
|---|---|---|
| T0-0 合成业务库 | **完成**（0.1 规模已在真实实例实跑，2026-10-22） | `evaluation/seed/ddl.sql`（12 表）、`seed/generate.py`、`scripts/clickhouse/init_ro_user.sql`；行数见附录 D |
| T0-1 评测集 | **6 / 30**（骨架可跑） | `datasets/v1_30.yaml` 6 条、`datasets/smoke.yaml` 3 条 |
| T0-2 golden 机制 | **完成** | golden 存 SQL、评测时现场执行；拒绝存 CSV 的过期问题 |
| T0-3 评测器 | **完成** | `ResultSetEvaluator` / `ClarificationEvaluator` / `FirstTryEvaluator` |
| T0-4 运行器与报告 | **完成** | `cli.py`（check/seed/run/report）、`report.py`（JSON/HTML/CSV） |
| T0-5 预检 | **完成** | `preflight.py`：连接、只读探针、12 表行数、数据锚点、用例合法性、golden 自检 |
| T0-6 失败归因 | **规则完成**，未跑真实失败 | `labeling.py`：OK/AMBIGUOUS/NO_TOOL/SCHEMA_MISSING/DIALECT_ERROR/TIMEOUT/SHAPE_MISMATCH/FILTER_MISSING/METRIC_WRONG/UNKNOWN |
| 目录与配置 | **完成** | `analytics_agent/evaluation/`；`pyproject.toml`（sdist 排除、`pythonpath`）、`tox.ini`（ruff 范围）、`.gitignore`（reports） |
| 配置加载 | **完成** | `config.py` 内置 `.env` 解析（无 `python-dotenv` 依赖），环境变量优先 |

### 已验证

| 验证项 | 结果 |
|---|---|
| `pytest tests/test_eval_compare.py tests/test_eval_config.py` | **9 passed**（行序、容差、形状、列别名、NULL/Decimal/日期归一化、`.env` 解析） |
| `ruff check analytics_agent tests/test_eval_compare.py` | **All checks passed** |
| `ruff format --check` | 通过 |
| `python -m analytics_agent.evaluation.cli --help` | 4 个子命令正常 |
| `EvaluationDataset.from_yaml` 解析两套用例 | 3 条 / 6 条，正常 |
| 报告生成（构造数据） | `report.json` / `report.html` / `failures.csv` 均可产出 |
| `seed --scale 0.1` 实跑 | 成功：12 表共 312,569 行；锚点 `2026-10-21`；见附录 D |
| 只读账号抽查数据 | 通过：测试账号 2.3%、USD 5.07%、重试流水 3.0%、GMV 非空且排序合理 |
| `check --dataset smoke.yaml` | **20/20 PASS**：连接、只读探针、12 表行数、数据锚点、3 条用例、golden `A-01`（4 行 / 0.02s） |

### 未完成 / 待执行（下一步）

- [x] **D1-2** `seed --scale 0.1` 已实跑（2026-10-22，见附录 D）
- [x] **D1-4** `check --dataset smoke.yaml` 全 PASS（20/20，2026-10-22，见附录 D）
- [ ] **D1-5** `run --dataset smoke.yaml` 冒烟评测（验证 LLM 链路与报告产出）
- [ ] **D2** 补齐 24 条用例（A 4 / B 5 / C 4 / D 3 / E 4 / F 2 / G 3 / H 2 / I 3 的剩余配额）
- [ ] **D3~D4** 首次 `run`，记录基线 EX / 首答命中 / 工具轮次 / P95
- [ ] **D5** 复跑稳定性验证，勾选 G0
- [ ] 待确认：`seed/generate.py` 的 1.0 规模（100 万 events）在本机耗时与内存
- [ ] 安全收紧：`readonly_user` 当前为 `GRANT SELECT ON *.*`，建议改为 `GRANT SELECT ON shop.*`

### 顺带修复的框架问题

`src/vanna/core/evaluation/dataset.py` 的 `from_yaml` / `from_json` / `save_yaml` / `save_json` 未指定编码，Windows 默认 GBK，中文用例会 `UnicodeDecodeError`。已统一为 `encoding="utf-8"`（提交 `1e1e565`）。

### 提交记录

| 提交 | 内容 |
|---|---|
| `a32b032` | 新增总计划 `AGENT_QUALITY_PLAN.md` |
| `f8c0e05` | 新增本手册 `STAGE0-EVAL.md` |
| `72d1633` | 评测目录统一为 `analytics_agent/evaluation` |
| `c294065` | 删除失效的 `src/evals/` |
| `685495b` | 文档与示例引用同步 |
| `1e1e565` | Stage 0 评测骨架（24 文件，含框架编码修复） |

---

## 0.2 前置阅读与关键决策

动手前先确认这三个决策，后面所有步骤都依赖它们：

| 决策 | 结论 | 理由 |
|---|---|---|
| 主评测数据 | **自建合成业务库**，不迁移 Spider/BIRD | 只有自建库才能定义口径、埋设真实陷阱、控制权限场景 |
| golden 形态 | **golden SQL**（评测时现场执行），不存 CSV | 数据按天滚动生成，存 CSV 会立刻过期；SQL 才是口径的载体 |
| 结果比较 | **结果集等价**，不比较 SQL 字符串 | 等价 SQL 写法太多，字符串匹配会严重低估 |

补充决策：

- `data-agent-mnist` 只用其**方法论与评分思想**，不引入 chDB、不引入模型评委作为主判分（无真实用户场景，成本与噪声不值得）。
- 数字容差**不采用**该仓库默认的“金额风格”（2 位小数、5% 相对漂移）——那是给合成榜单用的。我们的评测用 `rel_tol=1e-6`，避免把错误答案判成通过。
- Stage 0 先做 **30 题**跑通闭环；G0 通过后再扩到 100 题。不要一开始就攒 100 条。

---

## 1. 目标与 G0 门禁

### 1.1 目标

1. 有一个**可重复执行**的评测命令，输出可读报告。
2. 有一个**数据可控、口径可解释**的 ClickHouse 库，覆盖真实分析场景的难点。
3. 有 30 条分层用例，失败可归入固定分类。
4. 得到**基线 EX**，作为后续所有优化的对照。

### 1.2 G0 验收（全部满足才算通过）

> 状态：代码侧已就绪（`[x]`），运行时验收项仍未开始（`[ ]`）。详见 0.1。

- [x] **代码就绪**：`check` / `run` / `report` 三个命令已实现并通过静态检查与单元测试
- [x] **smoke 预检通过**：`check --dataset smoke.yaml` → 20/20 PASS（2026-10-22）
- [ ] 30 条用例齐备后复检：`check --strict` 全 PASS
- [ ] `python -m analytics_agent.evaluation.cli run` 一条命令跑完 30 题，产出 `report.json` + `report.html`（当前用例 6/30）
- [ ] 连续两次运行，EX 差异 ≤ 2pt（评测稳定）
- [ ] 100% 用例的失败都能落到 taxonomy 之一（`UNKNOWN` 占比 < 10%）
- [ ] 基线 EX、平均工具轮次、P95 延迟、单题 token 四项数字写入本文第 9 节
- [ ] 每个失败用例都有 `executed_sql` 与失败原因，可人工复盘

---

## 2. 总体设计

```
[数据层]  ClickHouse shop 库（合成数据，按天锚定）
          12 表 + shop.meta_data_anchor + 埋设的真实陷阱
              ▲ seed（幂等，固定 seed）
              │
[用例层]  analytics_agent/evaluation/datasets/*.yaml
          问题 + 分层 + golden_sql + 期望行为（澄清/拒答/权限）
              ▲ 读取
              │
[执行层]  Vanna Agent（被测对象） + EvaluationRunner
          采集 tool_calls（仅工具名 + 参数），SQL 由评测侧重放执行
              ▲ 判定
              │
[判定层]  ResultSetEvaluator / ClarificationEvaluator
          FirstTryEvaluator / EfficiencyEvaluator
              ▲ 汇总
              │
[报告层]  report.json / report.html / failures.csv
```

**为什么评测侧重放 SQL**：`core/evaluation` 的轨迹采集中间件只记录 `LlmRequest` 与模型请求的 `tool_calls`（工具名 + 参数），**不记录工具执行结果**。因此 judge 必须自己把 `run_sql.arguments["sql"]` 拿到 ClickHouse 重放，再与 golden SQL 的结果比较。这样还有个好处：错误信息可复现，失败分类不依赖埋点。

---

## 3. 从 `data-agent-mnist` 借什么、不借什么

该仓库是“搭自己的 agentic analytics benchmark”的脚手架，不是数据集。仓库自带 `examples/saas/`、`examples/clickhouse-dwh/` 两个 worked example（各 8 题），用 chDB 本地跑。

| 文件/能力 | 借不借 | 怎么用 |
|---|---|---|
| `bench/scoring.py` 结果集等价 + 列名映射 | **借思想** | 实现 Stage 0 的 `ResultSetEvaluator`：先按列名精确匹配，再走“列数/类型一致”的宽松匹配 |
| `07_failure_modes.py` 失败模式归类 | **借思想** | 对应本文第 7.6 节的 8 类 taxonomy（5 类太粗，我们按 Text2SQL 场景细分） |
| `schema_retrieval.py` 表级相关性排序 | **借** | Stage 3 的“候选表预筛”直接参考其思路；Stage 0 先不做 |
| `05_annotate.py` 多模型多数投票造 ground truth | **不借** | 你自己是业务专家，人工口径比模型投票可靠 |
| `bench/judge.py` 模型评委 | **不借（主判分）** | 仅可用于 Stage 4 的“口径解释是否清楚”这类主观项 |
| chDB 本地仓库 | **不借** | 直接用你的 ClickHouse 集群（可用性、类型、性能都更接近生产） |
| `verify_board.py` 完整性守卫 | **借思想** | 对应本文的“数据锚点 + 预检”机制，防止数据漂移导致分数漂移 |
| 默认数值容差（2 位小数 / 5% rel_tol） | **不借** | 太宽松，会把错答案判对；改为 `rel_tol=1e-6` |

**唯一需要真正读一遍的代码**：`bench/scoring.py`。读完后回答一个问题——“当列名不同但语义相同时，它如何判定？” 这决定了我们是否要给用例加 `column_alias` 配置。

---

## 4. 目录结构

### 4.1 放在哪里：根目录 `analytics_agent/`（单包，`evaluation/` 作为子包）

```
vanna/                              # 仓库根
├── src/vanna/                      # 框架本体，不改
├── tests/                          # 框架测试；我们的纯函数测试也放这里
├── docs/
│   ├── AGENT_QUALITY_PLAN.md
│   └── STAGE0-EVAL.md
├── analytics_agent/                # ← 业务代码（本项目唯一新增顶层包）
│   ├── __init__.py
│   ├── config.py                   # 连接、只读账号、路径、环境变量
│   ├── agent_factory.py            # 被测 Agent 的组装入口
│   ├── prompts/                    # Stage 1 起
│   ├── context/                    # schema catalog、enricher
│   ├── tools/                      # search_columns 等
│   ├── sql/                        # guardrails、结果归一化
│   └── evaluation/                 # ← 评测代码，业务包内的子包
│       ├── __init__.py
│       ├── cli.py                  # check / seed / run / report 子命令
│       ├── preflight.py            # 数据锚点、连接、权限、golden 自检
│       ├── executor.py             # 只读执行 SQL（重放 + golden）
│       ├── normalize.py            # DataFrame 归一化
│       ├── compare.py              # 结果集等价比较
│       ├── evaluators.py           # ResultSet / Clarification / FirstTry
│       ├── labeling.py             # 失败分类
│       ├── report.py               # json / html / csv
│       ├── datasets/
│       │   ├── smoke.yaml          # 3 条，开发用
│       │   └── v1_30.yaml          # 30 条，主评测
│       ├── golden/                 # 可选：数据冻结时的静态回归
│       └── seed/
│           ├── ddl.sql
│           ├── generate.py         # 合成数据生成
│           └── metrics.md          # 口径卡片
└── scripts/clickhouse/
    ├── init_ro_user.sql            # 只读账号
    └── healthcheck.sh
```

**为什么是 `evaluation/` 而不是 `eval/`**：

1. 与框架自身命名对齐（`vanna/core/evaluation/`），读代码时不会出现两套叫法。
2. 子包名 `eval` 虽然在全限定导入下**不会**遮蔽内置 `eval()`（内置函数在 `builtins`，不在模块命名空间），但 `import eval`、`python -m eval.cli` 这类写法容易被误读，也容易和第三方包重名。
3. 统一用 `python -m analytics_agent.evaluation.cli`，命令虽长一点，但语义明确；嫌长可加一个 `scripts/eval.py` 薄包装。

**为什么不放在根目录单开 `eval/`**：根目录会散落多个顶层包（`analytics_agent/` + `eval/`），Stage 1~5 每加一个模块都要往根目录塞东西；而且从根目录跑 `python -m eval.cli` 依赖 cwd，容易与框架的 `vanna` 混淆。

**为什么不放 `src/`**：`pyproject.toml` 里 `[tool.flit.module] path = "src/vanna"` 只发布 `vanna` 一个包；把业务代码放进 `src/` 会诱导你改打包配置，而这个项目短期内不需要被发布成 PyPI 包。

**为什么不另开仓库**：Stage 0~2 需要频繁对照框架源码（`core/evaluation`、`core/tool`），同仓库开发成本最低。将来要独立部署时，`analytics_agent/` 整个目录可以直接搬走，只留一条 `pip install -e /path/to/vanna`。

### 4.2 需要在仓库根做的三件配置

1. **`pyproject.toml` 的 sdist 排除**（避免业务代码混进框架的 sdist）：
   ```toml
   [tool.flit.sdist]
   exclude = [
       "frontends/", "tests/", "notebooks/", ".github/", "tox.ini",
       "analytics_agent/", "scripts/", "reports/",
   ]
   ```
2. **ruff / mypy 纳入新代码**（`tox.ini`）：
   ```ini
   [testenv:ruff]
   commands =
       ruff format --check src/vanna/ tests/ analytics_agent/
       ruff check src/vanna/ tests/ analytics_agent/
   ```
   mypy 暂不强制（业务代码处于快速迭代期），Stage 2 稳定后再加。
3. **`.gitignore` 增加运行产物**：
   ```gitignore
   reports/
   analytics_agent/evaluation/golden/*.csv
   ```

**测试放哪**：`normalize.py` / `compare.py` 这类纯函数测试放 `tests/test_eval_compare.py`（`pytest` 的 `testpaths=["tests"]` 会自动收集）；需要 ClickHouse 的测试加 `@pytest.mark.integration`，避免在无库环境失败。

### 4.3 存量 `src/evals/` 的处置：废弃，不迁移

仓库里已有 `src/evals/`（初始提交带入，两个文件）：

| 文件 | 现状 | 结论 |
|---|---|---|
| `src/evals/benchmarks/llm_comparison.py` | `get_sql_tools()` 返回空 `ToolRegistry`（注释写着 TODO）；`Agent(...)` 未传 `user_resolver` / `agent_memory`，按 2.0 的必填参数**直接跑不起来** | 废弃 |
| `src/evals/datasets/sql_generation/basic.yaml` | 8 条用例断言 `tools_called: ["generate_sql","execute_query"]`，这两个工具名在 2.0 中不存在（实际是 `run_sql`）；断言 `final_answer_contains: ["SELECT","GROUP BY"]` 与默认提示词“不要重复原始结果”冲突 | 废弃 |

它还是典型的“孤儿代码”：不在 `src/vanna`（flit 不打包）、不在 `tests/`（pytest 不收集）、不在 `tox` 的 ruff/mypy 范围，只会持续腐烂。

**可保留的两点精华**（已各有替代，不需要搬文件）：

1. `compare_agents` + `AgentVariant` + `save_html/save_csv` 的用法 → 见可运行的 `src/vanna/examples/evaluation_example.py`；Stage 5 的模型 A/B 直接照它写。
2. 8 条用例的“问题意图”（聚合、排名、时间序列、过滤、同环比、流失、CLV）→ 已并入本文 6.1 的分层矩阵，作为出题灵感。

**执行动作**：已执行 `git rm -r src/evals`（2026-10-22），并修正 `src/vanna/examples/evaluation_example.py` 末尾指向该文件的失效引用。若需找回，可用 `git log -- src/evals` 定位删除前的提交。

---

## 5. 合成业务库设计

### 5.1 设计原则

1. **形态真实**：星型模型 + 明细事件表，DateTime 粒度、金额用分、枚举用 `LowCardinality`。
2. **陷阱明确且可解释**：每个陷阱都对应一个失败分类，不允许“随机脏数据”。
3. **体量可控**：单机 ClickHouse 秒级查询，全量重新生成 < 3 分钟。
4. **可重复**：固定 `seed` + 固定锚点日期，任何人任何机器生成同一份数据。

### 5.2 表清单（12 张）

| 表 | 粒度 | 行数（目标） | 用途 |
|---|---|---|---|
| `dim_date` | 天 | ~800 | 日期维度、节假日 |
| `users` | 用户 | 50,000 | 留存、分群 |
| `products` | SKU | 2,000 | 品类分析 |
| `orders` | 订单 | 300,000 | GMV、客单价 |
| `order_items` | 订单行 | 900,000 | 品类/商品贡献 |
| `payments` | 支付流水 | 320,000 | 支付方式、重复扣款陷阱 |
| `refunds` | 退款 | 20,000 | 净收入 |
| `sessions` | 会话 | 400,000 | 转化漏斗 |
| `events` | 事件 | 1,000,000 | 行为分析、大表性能 |
| `campaigns` | 投放计划 | 200 | 渠道归因 |
| `accounts` | 订阅账户 | 8,000 | MRR、流失 |
| `meta_data_anchor` | 单行 | 1 | 数据锚点（预检用，`shop.meta_data_anchor`） |

### 5.3 DDL（`analytics_agent/evaluation/seed/ddl.sql`）

```sql
CREATE DATABASE IF NOT EXISTS shop;

CREATE TABLE IF NOT EXISTS shop.dim_date
(
    date_key       Date,
    year           UInt16,
    month          UInt8,
    day_of_week    UInt8,   -- 1=周一
    is_weekend     UInt8,
    is_holiday     UInt8
)
ENGINE = MergeTree
ORDER BY date_key;

CREATE TABLE IF NOT EXISTS shop.users
(
    user_id     UInt64,
    email       String,
    signup_at   DateTime('Asia/Shanghai'),
    channel     LowCardinality(String),   -- organic/paid_search/social/referral
    country     LowCardinality(String),
    is_test     UInt8 DEFAULT 0,          -- 陷阱①：测试账号
    deleted_at  Nullable(DateTime)        -- 陷阱②：软删
)
ENGINE = MergeTree
ORDER BY user_id;

CREATE TABLE IF NOT EXISTS shop.products
(
    product_id        UInt64,
    sku               String,
    name              String,
    category          LowCardinality(String),
    subcategory       Nullable(String),   -- 陷阱③：2% 为空，left join 易丢行
    unit_price_cents  Int64,
    cost_cents        Int64,
    is_active         UInt8
)
ENGINE = MergeTree
ORDER BY product_id;

CREATE TABLE IF NOT EXISTS shop.orders
(
    order_id        UInt64,
    user_id         UInt64,
    created_at      DateTime('Asia/Shanghai'),
    status          LowCardinality(String),  -- pending/paid/shipped/completed/cancelled/refunded
    currency        LowCardinality(String),  -- 陷阱④：95% CNY，5% USD（用 String 而非 FixedString，避免补零比较的坑）
    amount_cents    Int64,
    discount_cents  Int64,
    channel         LowCardinality(String),
    campaign_id     Nullable(UInt64),
    is_test         UInt8 DEFAULT 0
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (toDate(created_at), order_id);

CREATE TABLE IF NOT EXISTS shop.order_items
(
    order_id          UInt64,
    product_id        UInt64,
    quantity          UInt32,
    unit_price_cents  Int64,
    item_amount_cents Int64
)
ENGINE = MergeTree
ORDER BY (order_id, product_id);

CREATE TABLE IF NOT EXISTS shop.payments
(
    payment_id  UInt64,
    order_id    UInt64,
    paid_at     DateTime('Asia/Shanghai'),
    method      LowCardinality(String),   -- alipay/wechat/card
    amount_cents Int64,
    status      LowCardinality(String),   -- succeeded/failed
    is_retry    UInt8                     -- 陷阱⑤：重试流水，直接 sum 会重复计
)
ENGINE = MergeTree
ORDER BY (toDate(paid_at), payment_id);

CREATE TABLE IF NOT EXISTS shop.refunds
(
    refund_id     UInt64,
    order_id      UInt64,
    created_at    DateTime('Asia/Shanghai'),
    amount_cents  Int64,
    reason        LowCardinality(String),
    status        LowCardinality(String)  -- approved/rejected/pending
)
ENGINE = MergeTree
ORDER BY (toDate(created_at), refund_id);

CREATE TABLE IF NOT EXISTS shop.sessions
(
    session_id  UInt64,
    user_id     UInt64,
    started_at  DateTime('Asia/Shanghai'),
    channel     LowCardinality(String),
    device      LowCardinality(String),   -- ios/android/web
    converted   UInt8
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (toDate(started_at), session_id);

CREATE TABLE IF NOT EXISTS shop.events
(
    event_id    UInt64,
    user_id     UInt64,
    session_id  UInt64,
    event_time  DateTime('Asia/Shanghai'),
    event_name  LowCardinality(String),   -- page_view/add_to_cart/checkout/purchase
    properties  String                    -- JSON 字符串，陷阱⑥：大小写不统一
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (toDate(event_time), user_id, event_time);

CREATE TABLE IF NOT EXISTS shop.campaigns
(
    campaign_id  UInt64,
    name         String,
    channel      LowCardinality(String),
    start_date   Date,
    end_date     Date,
    budget_cents Int64
)
ENGINE = MergeTree
ORDER BY campaign_id;

CREATE TABLE IF NOT EXISTS shop.accounts
(
    account_id  UInt64,
    user_id     UInt64,
    plan        LowCardinality(String),   -- starter/pro/enterprise
    mrr_cents   Int64,
    status      LowCardinality(String),   -- active/paused/churned
    started_at  DateTime('Asia/Shanghai'),
    churned_at  Nullable(DateTime)
)
ENGINE = MergeTree
ORDER BY account_id;

CREATE TABLE IF NOT EXISTS shop.meta_data_anchor
(
    anchor_date  Date,
    seed         UInt64,
    generated_at DateTime
)
ENGINE = MergeTree
ORDER BY anchor_date;
```

> 用 `Int64` 存“分”，不用 Float，避免浮点误差污染结果比较。展示层统一 `/100.0`。

### 5.4 埋设的陷阱清单（每个都必须有对应用例）

| # | 陷阱 | 不处理会怎样 | 对应失败分类 |
|---|---|---|---|
| ① | `users.is_test=1` / `orders.is_test=1` 占 2% | GMV 虚高 | `FILTER_MISSING` |
| ② | 软删用户 `deleted_at IS NOT NULL` 占 1% | 用户数偏大 | `FILTER_MISSING` |
| ③ | `products.subcategory` 2% 为 NULL | `JOIN` 后丢行，品类汇总偏小 | `FILTER_MISSING` |
| ④ | 5% 订单为 USD | 直接 sum 得到混合币种 | `METRIC_WRONG` |
| ⑤ | `payments.is_retry=1` 占 3% | 支付金额重复计 | `METRIC_WRONG` |
| ⑥ | `events.properties` 中 `page_view` / `PageView` 混用 | 事件数偏小 | `FILTER_MISSING` |
| ⑦ | `orders.status` 含 `cancelled/refunded` | 口径错误 | `METRIC_WRONG` |
| ⑧ | 时区为 `Asia/Shanghai`，`events` 有 UTC 需求场景 | 日粒度错位 | `DIALECT_ERROR` |
| ⑨ | 指标“复购率”定义不唯一 | 各写各的 | `AMBIGUOUS` |
| ⑩ | `events` 100 万行，无时间过滤会全表扫 | 超时 | `TIMEOUT` |

### 5.5 数据生成规则（`analytics_agent/evaluation/seed/generate.py`）

```python
# 核心约束（伪代码，实现时补全）
rng = random.Random(SEED)                # 固定种子
anchor = date.today() - timedelta(days=1) # 数据截止到昨天

# 1) 用户：注册时间分布在 [anchor-730, anchor]
#    channel 权重: organic 40 / paid_search 30 / social 20 / referral 10
#    is_test: 2%；deleted_at: 1%
# 2) 订单：按用户活跃度加权；每天约 800~1200 单
#    status 权重: paid 35 / shipped 30 / completed 25 / pending 5 / cancelled 3 / refunded 2
#    currency: CNY 95% / USD 5%
#    amount_cents = sum(items) - discount，且 >= 100
# 3) 退款：仅 refunded 订单生成，金额 <= 订单金额
# 4) 支付：succeeded 为主，3% 生成 is_retry=1 的重复流水（金额相同）
# 5) 会话/事件：与订单弱关联，保证漏斗各步骤逐级递减
# 6) 订阅账户：mrr 与 plan 对应，churn 时间随机
```

实现要求：

- 全部使用 `random.Random(seed)`，**禁止** `random` 全局函数（保证可重复）。
- 批量写入用 `clickhouse_connect` 的 `client.insert(...)`，每批 5 万行。
- 生成结束写入 `shop.meta_data_anchor`：`(anchor, seed, now())`。
- 支持 `--drop`（重建）与 `--as-of YYYY-MM-DD`（补历史），默认不允许 `as-of` 早于今天 400 天。

### 5.6 指标口径卡片（`analytics_agent/evaluation/seed/metrics.md`）

这是“业务专家输入”的核心产物，也是 Stage 1/3 的术语表来源。**先写口径，再写 golden SQL**。

| 指标 | 定义 | 计算口径（针对本库） | 易错点 |
|---|---|---|---|
| GMV | 成交总额 | `orders` 中 `status IN ('paid','shipped','completed')` 且 `currency='CNY'`、`is_test=0` 的 `amount_cents` 之和，**不扣退款** | 含 USD、含取消单、含测试账号 |
| 净收入 | GMV - 退款 | 减去 `refunds.status='approved'` 的金额 | 忘记退款状态 |
| 客单价 AOV | GMV / 有效订单数 | 分母与 GMV 同口径 | 分母用了全量订单 |
| 支付金额 | 实际到账 | `payments.status='succeeded' AND is_retry=0` 的 `amount_cents` | 重复流水 |
| 复购率 | 期内下单 ≥2 次的用户占比 | 按 `orders` 去重用户 | 只统计首单用户 |
| 次日留存 | 当日新增用户次日有会话的比例 | `users.signup_at` vs `sessions.started_at` | 时区/日期边界 |
| 转化率 | 下单会话 / 总会话 | `sessions.converted=1` 占比 | 分子分母口径不一致 |
| MRR | 月度经常性收入 | `accounts.status='active'` 的 `mrr_cents` 之和 | 含 paused、含 churned |
| 渠道归因 | 订单归因到首触渠道 | `orders.channel`（本库定义为首触） | 误用 `events` 的末触 |

> 你作为业务专家需要**确认或修改**上表；确认后它就是后续 prompt 与指标工具的唯一权威来源（单一事实源）。

---

## 6. 用例设计（30 题）

### 6.1 分层矩阵

| 层 | 数量 | 测什么 | 期望行为 |
|---|---|---|---|
| A 单表聚合 | 4 | 基础统计、过滤 | 调用 `run_sql`，结果匹配 |
| B 多表 join | 5 | 星型模型、NULL join | 结果匹配 |
| C 时间窗口/同环比 | 4 | 日期函数、口径一致性 | 结果匹配 |
| D 留存/漏斗 | 3 | 多步计算、会话 | 结果匹配 |
| E 口径歧义 | 4 | 该不该澄清 | 先澄清或明确说明假设 |
| F 无法回答 | 2 | 拒答 | 不编造，不调用 `run_sql` |
| G 权限/RLS | 3 | 用户分组限制 | 拒绝越权数据 |
| H 大表/性能 | 2 | 分区裁剪、LIMIT | 30s 内返回 |
| I 多轮 | 3 | 指代与追问 | 继承上一轮上下文 |

### 6.2 用例字段规范

```yaml
- id: "A-01"
  user_id: "analyst"
  user_groups: ["analyst"]
  message: "最近 30 天各渠道的 GMV 是多少？"
  metadata:
    layer: "A"
    difficulty: "easy"
    golden_sql: |
      SELECT channel, SUM(amount_cents) / 100.0 AS gmv
      FROM shop.orders
      WHERE created_at >= now() - INTERVAL 30 DAY
        AND status IN ('paid','shipped','completed')
        AND currency = 'CNY'
        AND is_test = 0
      GROUP BY channel
      ORDER BY gmv DESC
    expected_behavior: "answer"        # answer | clarify | refuse
    order_sensitive: false
    column_alias: {}                   # 可选：{"gmv": "total_gmv"}
```

字段说明：

- `golden_sql`：**必须**能被只读账号执行且返回非空（F 类除外）。
- `expected_behavior`：`answer` / `clarify` / `refuse`，决定用哪个 evaluator。
- `order_sensitive`：默认 false（行序无关）。趋势类问题若必须有序，置 true。
- `column_alias`：当业务上允许不同列名时显式声明，避免宽松匹配误判。

### 6.3 完整样例（6 条，其余按模板补全）

```yaml
dataset:
  name: "shop_v1_30"
  description: "自建 ClickHouse 合成业务库评测集 v1"
  test_cases:

    - id: "A-01"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "最近 30 天各渠道的 GMV 是多少？"
      metadata:
        layer: A
        golden_sql: |
          SELECT channel, SUM(amount_cents) / 100.0 AS gmv
          FROM shop.orders
          WHERE created_at >= now() - INTERVAL 30 DAY
            AND status IN ('paid','shipped','completed')
            AND currency = 'CNY' AND is_test = 0
          GROUP BY channel ORDER BY gmv DESC
        expected_behavior: answer

    - id: "B-02"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "按品类看最近一个月的净收入"
      metadata:
        layer: B
        golden_sql: |
          SELECT p.category,
                 (SUM(oi.item_amount_cents) - SUM(IFNULL(r.amount_cents, 0))) / 100.0 AS net_revenue
          FROM shop.order_items oi
          JOIN shop.orders o ON o.order_id = oi.order_id
          JOIN shop.products p ON p.product_id = oi.product_id
          LEFT JOIN (
              SELECT order_id, SUM(amount_cents) AS amount_cents
              FROM shop.refunds WHERE status = 'approved' GROUP BY order_id
          ) r ON r.order_id = o.order_id
          WHERE o.created_at >= now() - INTERVAL 30 DAY
            AND o.status IN ('paid','shipped','completed')
            AND o.currency = 'CNY' AND o.is_test = 0
          GROUP BY p.category ORDER BY net_revenue DESC
        expected_behavior: answer

    - id: "C-03"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "本月 GMV 跟上月比增长了多少？"
      metadata:
        layer: C
        order_sensitive: false
        golden_sql: |
          WITH
            cur AS (
              SELECT SUM(amount_cents) AS v FROM shop.orders
              WHERE toStartOfMonth(created_at) = toStartOfMonth(today())
                AND status IN ('paid','shipped','completed')
                AND currency = 'CNY' AND is_test = 0
            ),
            prev AS (
              SELECT SUM(amount_cents) AS v FROM shop.orders
              WHERE toStartOfMonth(created_at) = toStartOfMonth(today() - INTERVAL 1 MONTH)
                AND status IN ('paid','shipped','completed')
                AND currency = 'CNY' AND is_test = 0
            )
          SELECT round((cur.v - prev.v) / prev.v * 100, 2) AS mom_pct FROM cur, prev
        expected_behavior: answer

    - id: "D-04"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "上周各渠道的支付转化率"
      metadata:
        layer: D
        golden_sql: |
          SELECT channel, countIf(converted = 1) / count() AS cvr
          FROM shop.sessions
          WHERE started_at >= toStartOfWeek(today()) - INTERVAL 7 DAY
            AND started_at <  toStartOfWeek(today())
          GROUP BY channel ORDER BY cvr DESC
        expected_behavior: answer

    - id: "E-05"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "看一下复购率"
      metadata:
        layer: E
        expected_behavior: clarify    # 未指定时间范围与统计粒度，应澄清
        golden_sql: null

    - id: "F-06"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "帮我查一下竞品的市场份额"
      metadata:
        layer: F
        expected_behavior: refuse     # 库内无竞品数据
        golden_sql: null
```

> 其余 24 条按同样结构补全。**先写 golden SQL 并在 ClickHouse 上跑通、看懂结果**，再录入 YAML。

### 6.4 出题纪律

1. 问题用真实业务口语，不要写“请写出 SQL 查询…”。
2. 同一指标至少两种问法（测检索/口径稳定性）。
3. 不要出“golden 有多种合理答案”的题；若口径确实不唯一，归入 E 类（应澄清）。
4. F 类必须是**数据上真的无法回答**，而不是“模型不会”。
5. 每道题的 golden 结果必须你本人看过，确认“这就是我要的数字”。

---

## 7. 评测实现

> 本章代码为设计骨架；**最终实现以 `analytics_agent/evaluation/` 下的代码为准**（已完成、通过 ruff 与单测）。主要差异：结果比较支持 `column_alias`；执行器统一走 `execute_safe()` 返回 `(df, error)`；未知错误按 taxonomy 回退。

### 7.1 预检（`analytics_agent/evaluation/preflight.py`）

`python -m analytics_agent.evaluation.cli check` 依次检查：

1. ClickHouse 可连接；账号为只读（尝试 `CREATE TABLE` 必须失败）。
2. `shop.meta_data_anchor.anchor_date == today - 1`（防止数据过期导致时间类用例失真）。
3. 12 张表存在且行数 > 0。
4. dataset 中 `id` 唯一；`expected_behavior` 合法。
5. 所有 `answer` 用例的 `golden_sql` 可执行、非空、耗时 < 5s。
6. 打印各层用例数量，与 6.1 矩阵一致。

任一项失败则退出码非 0。

### 7.2 只读执行器（`analytics_agent/evaluation/executor.py`）

```python
import clickhouse_connect
import pandas as pd

CLIENT = clickhouse_connect.get_client(
    host=..., port=..., username="readonly", password=..., database="shop",
)

async def execute_readonly(sql: str, timeout_s: int = 30) -> pd.DataFrame:
    result = CLIENT.query(
        sql,
        settings={
            "max_execution_time": timeout_s,   # 超时保护
            "max_result_rows": 10_000,          # 防止评测把内存打满
            "result_overflow_mode": "break",
        },
        # 真正的只读由账号权限保证（GRANT SELECT）。
        # 注意：connect 时若设 readonly=1，则不允许再改其它 setting（max_execution_time 会被拒）；
        # 需要连接级强制只读时用 readonly=2，或直接依赖只读账号。
    )
    return pd.DataFrame(result.result_rows, columns=result.column_names)
```

要求：

- 单个用例执行失败**不能**中断整个评测（返回 `QueryError` 对象）。
- 重放 agent 的 SQL 时必须限制 `max_rows`（建议 10,000），防止评测把集群拖垮。
- 记录每条 SQL 的耗时与返回行数。

### 7.3 归一化（`analytics_agent/evaluation/normalize.py`）

```python
def normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    # 数值统一到 6 位小数，消除浮点噪声
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round(6)
    # 空值统一
    out = out.where(pd.notna(out), None)
    return out
```

规则：

- 列名小写 + 去空格；不做下划线/驼峰转换（要转换必须在用例里显式配 `column_alias`）。
- 日期时间统一为 `YYYY-MM-DD HH:MM:SS` 字符串。
- `Decimal` 与 `float` 都转 `float` 后 round(6)。
- 布尔值统一为 `0/1`。

### 7.4 结果比较（`analytics_agent/evaluation/compare.py`）

```python
from dataclasses import dataclass


@dataclass
class CompareResult:
    equal: bool
    reason: str          # ok | shape | columns | values
    detail: str = ""


def results_equal(golden, actual, *, rel_tol=1e-6, order_sensitive=False) -> CompareResult:
    g, a = normalize(golden), normalize(actual)
    if g.shape != a.shape:
        return CompareResult(False, "shape", f"golden={g.shape} actual={a.shape}")

    if set(g.columns) != set(a.columns):
        return CompareResult(False, "columns", f"golden={list(g.columns)} actual={list(a.columns)}")

    a = a[g.columns]
    if not order_sensitive:
        g = g.sort_values(by=list(g.columns), kind="mergesort").reset_index(drop=True)
        a = a.sort_values(by=list(a.columns), kind="mergesort").reset_index(drop=True)

    for i in range(len(g)):
        for col in g.columns:
            gv, av = g.at[i, col], a.at[i, col]
            if isinstance(gv, float) and isinstance(av, float):
                if not math.isclose(gv, av, rel_tol=rel_tol, abs_tol=1e-9):
                    return CompareResult(False, "values", f"row {i} col {col}: {gv} != {av}")
            elif gv != av:
                return CompareResult(False, "values", f"row {i} col {col}: {gv!r} != {av!r}")
    return CompareResult(True, "ok")
```

**列名不匹配怎么办**：Stage 0 采用严格匹配（列名必须一致）。如果 agent 用了不同别名导致 `columns` 失败，先看是“模型没按要求命名”还是“业务上等价”。前者计入失败并进 taxonomy；后者在用例里加 `column_alias`。不要一上来就做模糊列匹配，否则会把错误判成通过。

### 7.5 Evaluator（`analytics_agent/evaluation/evaluators.py`）

```python
class ResultSetEvaluator(Evaluator):
    """answer 类用例：任意一条执行的 SQL 结果集与 golden 一致即通过。"""

    async def evaluate(self, test_case, agent_result) -> EvaluationResult:
        meta = test_case.metadata
        golden_sql = meta.get("golden_sql")
        sqls = [
            c["arguments"].get("sql", "")
            for c in agent_result.tool_calls
            if c.get("tool_name") == "run_sql"
        ]

        golden = await execute_readonly(golden_sql)
        for sql in reversed(sqls):
            actual = await execute_readonly(sql)
            cmp = results_equal(golden, actual, order_sensitive=meta.get("order_sensitive", False))
            if cmp.equal:
                return pass_result(test_case, sql)

        return fail_result(test_case, sqls, golden, reason=cmp.reason)
```

另外两个 evaluator：

- `ClarificationEvaluator`：`expected_behavior == "clarify"` 时，检查最终回答是否包含澄清问句且**未调用** `run_sql`；`answer` 类则反过来，不允许无意义反问。
- `FirstTryEvaluator`：只看第一次 `run_sql` 是否命中 golden，用于衡量“一次写对率”，与 EX 分开统计。

效率类直接用仓库内置 `EfficiencyEvaluator`；口径主观项留到 Stage 4 再接 `LLMAsJudgeEvaluator`。

### 7.6 失败分类（`analytics_agent/evaluation/labeling.py`）

优先级从上到下匹配，命中即停：

| 顺序 | 条件 | code |
|---|---|---|
| 1 | `expected_behavior=clarify` 但未澄清 / `refuse` 但编造 | `AMBIGUOUS` |
| 2 | 未调用 `run_sql` 且未澄清 | `NO_TOOL` |
| 3 | SQL 报错含 `Unknown identifier` / `Missing columns` / `unknown table` | `SCHEMA_MISSING` |
| 4 | SQL 报错含 `Syntax error` / `Unsupported` / `Cannot parse` | `DIALECT_ERROR` |
| 5 | 报错含 `Timeout` / `max_execution_time` / code 159 | `TIMEOUT` |
| 6 | SQL 成功但结果形状/列名不符 | `SHAPE_MISMATCH` |
| 7 | SQL 成功、形状一致、数值不符 → 与“去掉过滤条件”的对照 SQL 结果一致 | `FILTER_MISSING` |
| 8 | 其余数值不符 | `METRIC_WRONG` |
| 9 | 以上都不满足 | `UNKNOWN`（人工标注） |

`FILTER_MISSING` 的判定是可选的进阶项：用例里可提供 `metadata.without_filter_sql`（例如去掉 `is_test=0`），若 agent 结果恰好匹配它，就能精准归因。**建议至少 5 条用例配此字段**，否则 7 与 8 难区分。

### 7.7 CLI（`analytics_agent/evaluation/cli.py`）

```bash
python -m analytics_agent.evaluation.cli check                       # 预检 + golden 自检
python -m analytics_agent.evaluation.cli seed --drop --as-of 2026-10-21
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/smoke.yaml      # 3 条，开发用
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml --out reports/2026-10-22
python -m analytics_agent.evaluation.cli report --run reports/2026-10-22             # 生成 html
```

`run` 的职责：

1. 构造被测 Agent（`analytics_agent.agent_factory.build_agent()`）。
2. 用 `EvaluationRunner(evaluators=[...], max_concurrency=1)` 跑用例。
   **Stage 0 固定并发 1**：工具轨迹与用例的对应关系最可靠，30 题耗时可接受。
3. 对每个用例补充 `labeling.classify(...)`。
4. 输出 `report.json`（机器可读）、`report.html`（人读）、`failures.csv`（复盘用）。

`report.json` 结构：

```json
{
  "run_id": "2026-10-22T10-00-00",
  "dataset": "shop_v1_30",
  "agent_config": {"model": "...", "temperature": 0.0, "max_tool_iterations": 8},
  "summary": {"ex": 0.0, "ex_first_try": 0.0, "avg_tool_calls": 0.0, "p95_latency_ms": 0, "avg_tokens": 0},
  "by_layer": {"A": {"total": 4, "passed": 0}},
  "by_taxonomy": {"SCHEMA_MISSING": 0},
  "cases": []
}
```

---

## 8. 每日工作步骤

> 每天开始先 `python -m analytics_agent.evaluation.cli check`，结束前提交代码与报告。
> **每完成一节的操作，必须在附录 D 追加一行执行记录**（命令、结果、关键数字、报告路径）。

### D1：把数据和连接跑通（代码已就绪，按顺序执行即可）

> 不需要手工建库建表：`seed` 会在同一个进程里执行 `ddl.sql`（`CREATE DATABASE shop` + 12 张表），
> 再灌入合成数据并写入 `meta_data_anchor`。
> 唯一需要人工的是**创建只读账号**——授予权限属于 DBA 动作，不应藏进评测脚本。

1. **配置环境变量**：`copy .env.example .env`，填 `CH_HOST`、`CH_PORT`、
   `CH_WRITE_USER`、`CH_WRITE_PASSWORD`（灌数账号需要 `CREATE/INSERT/TRUNCATE`）。
   `.env` 由 `analytics_agent/config.py` 自带的解析器加载（无 `python-dotenv` 依赖），
   已存在的环境变量优先。
2. **灌数**（默认锚点为昨天；先用 0.1 规模验证逻辑，约 30 秒）：
   ```bash
   python -m analytics_agent.evaluation.cli seed --scale 0.1
   ```
   输出每张表的行数；`--drop` 可重建，`--as-of YYYY-MM-DD` 可指定锚点。
3. **创建只读账号**（必须在 seed 之后：`GRANT SELECT ON shop.*` 要求库已存在）：
   用任意 ClickHouse 客户端执行 `scripts/clickhouse/init_ro_user.sql`，
   把 `<password>` 换成真实口令；或手工执行等价的两条 SQL：
   ```sql
   CREATE USER IF NOT EXISTS readonly_user IDENTIFIED WITH sha256_password BY 'hh000000';
   GRANT SELECT ON *.* TO readonly_user;
   ```
   然后把 `CH_USER=readonly`、`CH_PASSWORD=<password>` 写回 `.env`。
4. **预检**：
   ```bash
   python -m analytics_agent.evaluation.cli check --dataset analytics_agent/evaluation/datasets/smoke.yaml
   ```
   重点确认 `clickhouse.readonly`、`data.anchor`、各表行数、`golden.*` 全 PASS。
5. **冒烟评测**（3 条，验证 LLM 链路）：
   ```bash
   python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/smoke.yaml
   ```

**DoD**：`check` 全 PASS；`run` 产出 `reports/<timestamp>/report.html`；单条用例可人工复盘。

**备注**：如果暂时没有权限建只读账号，可临时把 `CH_USER` 设为灌数账号，此时
`clickhouse.readonly` 会 FAIL（预期行为），但不要带着这个状态进入 G0 评审。

### D2：口径与 golden

1. 确认 `metrics.md` 中 9 个指标口径（可增删）。
2. 写 6.3 的 6 条样例，逐条在 ClickHouse 执行，人工核对结果。
3. 补全到 30 条，按 6.1 矩阵分配。
4. 为 5 条用例补 `without_filter_sql`。
5. `python -m analytics_agent.evaluation.cli check` 做 golden 自检（非空、<5s）。

**DoD**：30 条 golden SQL 全部可执行；每条你都能解释数字来源。

### D3：evaluator 与 runner

1. 实现 `normalize.py` / `compare.py`，用 3 组构造数据做单测：
   - 完全一致 → pass
   - 行序不同 → pass
   - 数值差 1e-3 → fail
2. 实现 `ResultSetEvaluator` / `ClarificationEvaluator` / `FirstTryEvaluator`。
3. 实现 `cli.py` 的 `run` 与 `report`。
4. 用 `smoke.yaml`（3 条）跑通全链路。

**DoD**：`smoke.yaml` 能产出 `report.json` 与 `report.html`，失败用例带 `executed_sql`。

### D4：跑基线、做归因

1. `check` → `run v1_30` → `report`。
2. 记录四项指标。
3. 逐条看失败，标注 taxonomy；`UNKNOWN` 手工归类并回补规则。
4. 挑 3 条最典型的失败写入本文第 10 节「基线观察」。

**DoD**：`UNKNOWN` < 10%；失败原因无“看不懂为什么错”的用例。

### D5：稳定性验证与 G0 评审

1. 同配置再跑一次，比较 EX 波动（应 ≤ 2pt）。
2. 换 `temperature=0` 与默认值各跑一次，确认配置已生效（结果不同才说明参数真传下去了）。
3. 归档报告到 `reports/`，在本文第 9 节填写基线。
4. 对照第 1.2 节逐项勾选 G0。

**DoD**：G0 全绿，进入 Stage 1。

---

## 9. 基线记录（跑完后填写）

> 当前状态：**未测量**。评测代码已就绪，等待 D1 灌数、D2 补齐用例、D4 首跑。

| 指标 | 数值 | 备注 |
|---|---|---|
| 数据集版本 | `shop_v1_30` | |
| 模型 | | |
| 配置 | `temperature=0.0, max_tool_iterations=8` | |
| EX | `____%` | 第 1 次 |
| EX（复跑） | `____%` | 第 2 次，波动 `____pt` |
| EX（首答命中） | `____%` | 只看第一次 `run_sql` |
| 平均工具轮次 | `____` | |
| P95 延迟 | `____ ms` | |
| 平均 token/题 | `____` | |
| 失败分布 | 见 `by_taxonomy` | |

### 基线观察（人工填写 3 条）

1. 
2. 
3. 

---

## 10. 常见坑

| 坑 | 现象 | 处理 |
|---|---|---|
| 数据按天滚动但 golden 存 CSV | 隔天 EX 大跌 | golden 用 SQL 现场执行；或冻结数据快照 |
| 数值容差过松 | 错答案被判通过 | `rel_tol=1e-6`，只对金额展示层做 2 位小数 |
| 列名模糊匹配 | 模型乱起别名也通过 | Stage 0 严格匹配；等价别名在用例里显式声明 |
| 并发跑评测 | 轨迹与用例串号 | Stage 0 `max_concurrency=1` |
| golden 自己写错 | 优化方向被带偏 | 每条人工核对；golden 进 git，改动需 review |
| 只读账号没验证 | 评测期误写库 | 预检尝试建表必须失败 |
| 时间函数依赖 `now()` | 跨零点跑出差异 | 数据集锚点 `today-1`；评测在单日内完成，或记录 `run_at` 并接受微小差异 |
| 用模型生成 golden | 口径漂移且不可解释 | 只由业务专家产出；模型仅用于交叉检查 |

---

## 11. 附录

### A. 只读账号

```sql
CREATE USER IF NOT EXISTS readonly IDENTIFIED WITH sha256_password BY '<password>';
GRANT SELECT ON shop.* TO readonly;
-- 可选：连接级加固。注意 readonly=1 会禁止改其它 setting（包括 max_execution_time），
-- 若要同时用查询级 setting，请用 readonly=2。
-- ALTER USER readonly SETTINGS readonly = 2;
```

> 预检时用该账号执行一次 `CREATE TABLE shop.__probe (x UInt8) ENGINE = Memory`，必须失败；这是“只读”的唯一可信验证。

### B. 报告页建议字段

每行：`id | layer | 期望行为 | 实际行为 | EX | 首答 | 工具轮次 | 耗时 | taxonomy | executed_sql（截断）`。

顶部：四项汇总 + 各层通过率 + taxonomy 柱状图。

### C. 与主计划的对应关系

| 主计划任务 | 本文对应 |
|---|---|
| T0-0 合成业务库 | 第 5 章（DDL、陷阱、生成规则、口径卡片） |
| T0-1 评测集 | 第 6 章（30 条分层矩阵与用例规范） |
| T0-2 golden | 6.2 / 7.2 / 7.4（改为 golden SQL 现场执行） |
| T0-3 `ResultSetEvaluator` | 7.3~7.5 |
| T0-4 运行器与报告 | 7.7 / 第 8 章 |
| T0-5 预检 | 7.1 |
| T0-6 失败归因 | 7.6 |
| G0 门禁 | 1.2 |

### D. 执行日志（每完成一步追加一行）

> 约定：每次配置变更、`seed`、`check`、`run`、复跑之后，都在本表追加记录。
> **不记录口令与完整查询结果**，只记录可复核的事实（行数、耗时、通过/失败项、报告路径）。

| 日期 | 步骤 | 操作 | 结果 | 备注 |
|---|---|---|---|---|
| 2026-10-22 | D1-0 | 配置 `.env`（`CH_HOST=192.168.0.193`、`CH_PORT=18123`、`CH_DATABASE=shop`） | 成功 | 查询账号 `readonly_user`；口令不入库 |
| 2026-10-22 | D1-0 | 创建只读账号并授权 | 成功 | 当前为 `GRANT SELECT ON *.*`，建议收紧到 `shop.*` |
| 2026-10-22 | D1-2 | `seed --scale 0.1` | 成功 | 12 表 312,569 行；明细见下 |
| 2026-10-22 | D1-2 | 只读账号抽查（锚点/测试账号/币种/状态/重试/GMV） | 通过 | 锚点 `2026-10-21`，seed=42 |
| 2026-10-22 | D1-4 | `check --dataset smoke.yaml` | **20/20 PASS** | 只读探针生效；golden `A-01` 4 行 / 0.02s |

**2026-10-22 · D1-2 灌数明细（scale=0.1）**

| 表 | 行数 | 表 | 行数 |
|---|---|---|---|
| dim_date | 1,201 | refunds | 1,101 |
| users | 5,000 | sessions | 40,000 |
| products | 200 | events | 100,000 |
| campaigns | 200 | accounts | 800 |
| orders | 36,000 | order_items | 89,916 |
| payments | 34,160 | meta_data_anchor | 1 |

**抽查结论**

- 测试账号订单 837 / 36,000 ≈ 2.3%（预期 ~2%）
- USD 订单 1,826 / 36,000 ≈ 5.07%（预期 5%）
- 重试流水 1,029 / 34,160 ≈ 3.0%（预期 3%）
- 近 30 天 GMV 按渠道非空且排序合理（organic > paid_search > social > referral）
- 只读账号可查询，说明 `.env` 的 `CH_USER` / `CH_PASSWORD` 生效
