# Stage 0 执行手册：评测基线与 ClickHouse 合成业务库

> 本文是 `docs/AGENT_QUALITY_PLAN.md` 中 **Stage 0 / T0-0 ~ T0-6** 的详细执行版。
> 完成本文全部验收项（G0）后，才进入 Stage 1（静态上下文工程）。
>
> - 状态：**Stage 0 完成**（G0 有条件通过：仅“稳定性复跑”延后至 Stage 1 后补，2026-10-23）
> - 主评测库：自建 ClickHouse 合成业务库（`shop`）
> - 方法论参考：`ClickHouse/data-agent-mnist`（Apache-2.0）
> - 业务专家：项目负责人本人（负责口径与 golden SQL 审核）
> - 最后更新：2026-10-23

---

## 0.1 实施状态（2026-10-27 更新）

### 已完成

| 任务 | 状态 | 产物 / 证据 |
|---|---|---|
| T0-0 合成业务库 | **完成**（0.1 规模已在真实实例实跑，2026-10-22） | `evaluation/seed/ddl.sql`（12 表）、`seed/generate.py`、`scripts/clickhouse/init_ro_user.sql`；行数见附录 D |
| T0-1 评测集 | **30 / 30**（22 条 answer golden 全部可执行；业务已核对） | `datasets/v1_30.yaml` 30 条、`datasets/smoke.yaml` 4 条 |
| T0-2 golden 机制 | **完成** | golden 存 SQL、评测时现场执行；拒绝存 CSV 的过期问题 |
| T0-3 评测器 | **完成** | `ResultSetEvaluator` / `ClarificationEvaluator` / `FirstTryEvaluator` |
| T0-4 运行器与报告 | **完成** | `cli.py`（check/seed/run/report）、`report.py`（JSON/HTML/CSV） |
| T0-5 预检 | **完成** | `preflight.py`：连接、只读探针、12 表行数、数据锚点、用例合法性、golden 自检 |
| T0-6 失败归因 | **完成**；Stage 1/2 真实失败已按 taxonomy 归因 | `labeling.py`：OK/AMBIGUOUS/NO_TOOL/SCHEMA_MISSING/DIALECT_ERROR/TIMEOUT/SHAPE_MISMATCH/FILTER_MISSING/METRIC_WRONG/UNKNOWN |
| 目录与配置 | **完成** | `analytics_agent/evaluation/`；`pyproject.toml`（sdist 排除、`pythonpath`）、`tox.ini`（ruff 范围）、`.gitignore`（reports） |
| 配置加载 | **完成** | `config.py` 内置 `.env` 解析（无 `python-dotenv` 依赖），环境变量优先 |

### 已验证

| 验证项 | 结果 |
|---|---|
| `pytest tests/test_eval_compare.py tests/test_eval_config.py tests/test_eval_evaluators.py tests/test_deepseek_integration.py` | **25 passed**（比较/归一化、`.env`、澄清与拒答、DeepSeek 集成与 temperature 透传） |
| `ruff check analytics_agent tests/test_eval_compare.py` | **All checks passed** |
| `ruff format --check` | 通过 |
| `python -m analytics_agent.evaluation.cli --help` | 4 个子命令正常 |
| `EvaluationDataset.from_yaml` 解析两套用例 | 3 条 / 6 条，正常 |
| 报告生成（构造数据） | `report.json` / `report.html` / `failures.csv` 均可产出 |
| `seed --scale 0.1` 实跑 | 成功：12 表共 312,569 行；锚点 `2026-10-21`；见附录 D |
| 只读账号抽查数据 | 通过：测试账号 2.3%、USD 5.07%、重试流水 3.0%、GMV 非空且排序合理 |
| `check --dataset smoke.yaml` | **20/20 PASS**：连接、只读探针、12 表行数、数据锚点、3 条用例、golden `A-01`（4 行 / 0.02s） |
| `run --dataset smoke.yaml`（4 条） | 管线验证通过：EX 50%、首答 50%、behavior 0%、avg_tools 0.5；`A-02` 走通 `run_sql` 并命中（详见附录 D） |
| `check --strict --dataset v1_30.yaml` | **42/42 PASS**（2026-10-27）：30 条用例、22 条 golden（各 <0.1s）、5 条 `without_filter_sql` 破坏性验证全部“结果有变化” |
| `seed` 重建（2026-10-23） | 锚点更新为 `2026-10-22`，12 表行数与 10-22 一致；重建后 `check --strict` 仍 41/41 |
| `seed` 重灌（时间戳封顶） | 各事实表 `max(时间戳)` 均为 `2026-10-22 23:59:59`，无未来数据；`check --strict` 41/41 |
| DDL 注释与 `--schema-only` 重建（2026-10-24） | 12 张表、77 个字段 **100% 有 COMMENT**；表已重建为空，锚点未写，跑评测前需重新灌数 |
| `preview` 子命令 | 逐条打印问题/期望/golden 结果，供 D2-2 审核使用 |
| `run --dataset smoke.yaml`（DeepSeek） | 4 条：EX 50%、首答 50%、behavior 0%、avg_tools 5.5；`A-02` 1 次 `run_sql` 命中；3 条触发 5 轮上限 |

> 说明：上表中 41/41 是 B-05/22 条 golden 之前的历史记录；当前预检为 **42/42**。

### 当前状态（2026-10-27，Stage 3 入口）

- 数据已重灌，锚点 **2026-10-26**；
- `check --strict` **42/42 PASS**（30 条用例、22 条 answer golden）；
- 评测集已完成 B-05（原 E-05）归类、D-17/D-18/D-04/C-03/C-15/C-16/A-09 等 golden 修正；
- Stage 0 工作已收尾，Stage 1/2 结果见各自文档。

### 历史待办（Stage 0 收尾时记录，均已完成或豁免）

- [x] **重新灌数**：2026-10-27 已重灌至锚点 `2026-10-26`；`check --strict` 42/42

- [x] **D1-2** `seed --scale 0.1` 已实跑（2026-10-22，见附录 D）
- [x] **D1-4** `check --dataset smoke.yaml` 全 PASS（20/20，2026-10-22，见附录 D）
- [x] **D1-5** `run --dataset smoke.yaml` 冒烟评测完成（4 条，管线跑通；质量基线待 D2 后测）
- [x] **Stage 1 输入已明确**：schema/方言 prompt、澄清策略、拒答策略（见附录 D 的观察与第 9 节）
- [x] **D2-0** 5 项口径已裁决（2026-10-22，写入 `seed/metrics.md`）
- [x] **D2-1** 24 条用例已产出并合并进 `v1_30.yaml`（golden 逐条执行验证）
- [x] **D2-2 业务核对**：A~I 全部核对完成（2026-10-23）
- [x] **重新 seed**：已完成（2026-10-27），时间戳封顶生效；锚点 `2026-10-26`，`check --strict` 42/42
- [x] **复核 A~D 数值**：抽检通过（2026-10-23），无需全量复核
- [x] **B1** DeepSeek 冒烟（2026-10-23）：API 与工具调用正常，`A-02` 通过
- [x] **B2** 首次跑 30 条（2026-10-23）：实测 EX 0%（0/21）、avg_tools 9.83，18 条触达 8 轮上限；评分规则已修正，按裁定**以 15% 估计记录基线、不重跑**
- [x] **G0** 有条件未全绿：仅“稳定性复跑”未测，经裁定先进入 Stage 1
- [x] **评分规则修正**：列投影（`projection`）+ “近 N 天”自然日窗口（9 条 golden）
- [ ] **D5** 复跑稳定性验证 —— **豁免**：延后至 Stage 1 后补（G0 有条件通过）

### 顺带修复的框架问题

`src/vanna/core/evaluation/dataset.py` 的 `from_yaml` / `from_json` / `save_yaml` / `save_json` 未指定编码，Windows 默认 GBK，中文用例会 `UnicodeDecodeError`。已统一为 `encoding="utf-8"`（提交 `1e1e565`）。

`OllamaLlmService` 只使用构造参数里的 `temperature` / `num_ctx`，**不读取 `LlmRequest.temperature`**，导致 `AgentConfig(temperature=0.0)` 对 Ollama 形同虚设。已在 `agent_factory.build_llm_service()` 中显式传 `temperature=0.0`，并新增 `LLM_NUM_CTX` 环境变量（默认 8192）。

框架的 `OpenAILlmService._build_payload()` 原本也不透传 temperature。已改为：`OpenAILlmService` 增加可选构造参数 `temperature`（默认 `None` 不发送，保持 o1/o3 兼容），DeepSeek 集成默认 `0.0`。DeepSeek 本体放在 `src/vanna/integrations/deepseek/`，业务侧只保留工厂分支（详见附录 F）。

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

> 状态：**有条件未全绿**。除“稳定性复跑”外均已满足；经裁定先进入 Stage 1，稳定性复跑留待 Stage 1 后补。

- [x] **代码就绪**：`check` / `run` / `report` 三个命令已实现并通过静态检查与单元测试
- [x] **smoke 预检通过**：`check --dataset smoke.yaml` → 20/20 PASS（2026-10-22）
- [x] **30 条用例齐备后复检**：`check --strict` → 41/41 PASS（2026-10-23）
- [x] `run` 一条命令跑完 30 题，产出 `report.json` + `report.html`（2026-10-23，跑后修正评分规则）
- [ ] 连续两次运行，EX 差异 ≤ 2pt —— **未测**，Stage 1 后补
- [x] 失败全部落到 taxonomy（`UNKNOWN` = 0%）：`SHAPE_MISMATCH` 21 / `AMBIGUOUS` 5
- [x] 基线 EX、平均工具轮次、P95、单题 token 已写入第 9 节（EX 为保守估计 15%）
- [x] 每个失败用例都有 `executed_sql` 与失败原因

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
-- Stage 0 合成业务库 DDL（ClickHouse）
-- 金额一律用 Int64 存“分”，避免浮点误差污染评测比较。
-- 所有表与字段都带 COMMENT：Stage 1 的 schema 目录直接读 system.columns.comment。

CREATE DATABASE IF NOT EXISTS shop;

CREATE TABLE IF NOT EXISTS shop.dim_date
(
    date_key     Date COMMENT '日期主键，格式 YYYY-MM-DD',
    year         UInt16 COMMENT '年份，如 2026',
    month        UInt8 COMMENT '月份，1-12',
    day_of_week  UInt8 COMMENT '星期几，1=周一 ... 7=周日',
    is_weekend   UInt8 COMMENT '是否周末，1=是，0=否',
    is_holiday   UInt8 COMMENT '是否法定节假日，1=是，0=否'
)
ENGINE = MergeTree
ORDER BY date_key
COMMENT '日期维度表，用于关联时间属性';

CREATE TABLE IF NOT EXISTS shop.users
(
    user_id     UInt64 COMMENT '用户唯一 ID',
    email       String COMMENT '注册邮箱',
    signup_at   DateTime('Asia/Shanghai') COMMENT '注册时间（Asia/Shanghai）',
    channel     LowCardinality(String) COMMENT '注册渠道（首触）；取值 organic/paid_search/social/referral',
    country     LowCardinality(String) COMMENT '注册国家；取值 CN/US/JP/SG',
    is_test     UInt8 DEFAULT 0 COMMENT '测试账号标记，1=测试；经营指标必须排除',
    deleted_at  Nullable(DateTime) COMMENT '软删时间；非空表示已注销，经营指标需排除'
)
ENGINE = MergeTree
ORDER BY user_id
COMMENT '用户表，一行一个注册用户';

CREATE TABLE IF NOT EXISTS shop.products
(
    product_id        UInt64 COMMENT '商品唯一 ID',
    sku               String COMMENT '商品编码',
    name              String COMMENT '商品名称',
    category          LowCardinality(String) COMMENT '商品品类；取值 electronics/apparel/grocery/home/beauty',
    subcategory       Nullable(String) COMMENT '二级品类；约 2% 为空，left join 时注意 NULL 丢行',
    unit_price_cents  Int64 COMMENT '售价（分）',
    cost_cents        Int64 COMMENT '成本（分）',
    is_active         UInt8 COMMENT '是否在售，1=是，0=否'
)
ENGINE = MergeTree
ORDER BY product_id
COMMENT '商品维表，含品类与成本';

CREATE TABLE IF NOT EXISTS shop.orders
(
    order_id        UInt64 COMMENT '订单唯一 ID',
    user_id         UInt64 COMMENT '下单用户 ID',
    created_at      DateTime('Asia/Shanghai') COMMENT '下单时间（Asia/Shanghai）',
    status          LowCardinality(String) COMMENT '订单状态；有效订单=paid/shipped/completed；另有 pending/cancelled/refunded',
    currency        LowCardinality(String) COMMENT '币种；95% CNY，5% USD；经营指标默认只看 CNY',
    amount_cents    Int64 COMMENT '订单金额（分），已扣 discount_cents；不含退款',
    discount_cents  Int64 COMMENT '优惠金额（分）',
    channel         LowCardinality(String) COMMENT '首触渠道；取值 organic/paid_search/social/referral',
    campaign_id     Nullable(UInt64) COMMENT '关联营销活动 ID；可为空',
    is_test         UInt8 DEFAULT 0 COMMENT '测试订单标记，1=测试；经营指标必须排除'
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (toDate(created_at), order_id)
COMMENT '订单主表，一单一行；金额单位为分';

CREATE TABLE IF NOT EXISTS shop.order_items
(
    order_id          UInt64 COMMENT '所属订单 ID',
    product_id        UInt64 COMMENT '商品 ID',
    quantity          UInt32 COMMENT '购买数量',
    unit_price_cents  Int64 COMMENT '成交单价（分）',
    item_amount_cents Int64 COMMENT '明细金额（分）= quantity * unit_price_cents - 分摊优惠'
)
ENGINE = MergeTree
ORDER BY (order_id, product_id)
COMMENT '订单明细表，一单多行；金额单位为分';

CREATE TABLE IF NOT EXISTS shop.payments
(
    payment_id   UInt64 COMMENT '支付流水 ID',
    order_id     UInt64 COMMENT '所属订单 ID',
    paid_at      DateTime('Asia/Shanghai') COMMENT '支付时间（Asia/Shanghai）',
    method       LowCardinality(String) COMMENT '支付方式；取值 alipay/wechat/card',
    amount_cents Int64 COMMENT '支付金额（分）',
    status       LowCardinality(String) COMMENT '支付状态；当前数据只生成 succeeded',
    is_retry     UInt8 COMMENT '重试流水标记，1=重试；统计金额必须排除 is_retry=1'
)
ENGINE = MergeTree
ORDER BY (toDate(paid_at), payment_id)
COMMENT '支付流水表；含 3% 重复扣款重试流水';

CREATE TABLE IF NOT EXISTS shop.refunds
(
    refund_id     UInt64 COMMENT '退款单 ID',
    order_id      UInt64 COMMENT '所属订单 ID；退款是订单级，没有 product_id',
    created_at    DateTime('Asia/Shanghai') COMMENT '退款发起时间（Asia/Shanghai）',
    amount_cents  Int64 COMMENT '退款金额（分）',
    reason        LowCardinality(String) COMMENT '退款原因；取值 quality/late_delivery/wrong_item/other',
    status        LowCardinality(String) COMMENT '退款状态；当前数据只有 approved/pending，统计只算 approved'
)
ENGINE = MergeTree
ORDER BY (toDate(created_at), refund_id)
COMMENT '退款表，订单级（无 product_id）；金额单位为分';

CREATE TABLE IF NOT EXISTS shop.sessions
(
    session_id  UInt64 COMMENT '会话 ID',
    user_id     UInt64 COMMENT '用户 ID',
    started_at  DateTime('Asia/Shanghai') COMMENT '会话开始时间（Asia/Shanghai）',
    channel     LowCardinality(String) COMMENT '本次会话渠道；与 orders.channel（首触）不同',
    device      LowCardinality(String) COMMENT '设备类型；取值 ios/android/web',
    converted   UInt8 COMMENT '是否转化（下单），1=是，0=否'
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (toDate(started_at), session_id)
COMMENT '会话表，一行一次访问，用于转化率与漏斗';

CREATE TABLE IF NOT EXISTS shop.events
(
    event_id    UInt64 COMMENT '事件 ID',
    user_id     UInt64 COMMENT '用户 ID',
    session_id  UInt64 COMMENT '所属会话 ID',
    event_time  DateTime('Asia/Shanghai') COMMENT '事件时间（Asia/Shanghai）',
    event_name  LowCardinality(String) COMMENT '事件名；取值 page_view/add_to_cart/checkout/purchase',
    properties  String COMMENT '事件属性 JSON 字符串；键名大小写不统一（如 event/Event），解析时需兼容'
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (toDate(event_time), user_id, event_time)
COMMENT '行为事件明细表，一行一个事件；100 万行，查询必须带时间过滤';

CREATE TABLE IF NOT EXISTS shop.campaigns
(
    campaign_id  UInt64 COMMENT '活动 ID',
    name         String COMMENT '活动名称',
    channel      LowCardinality(String) COMMENT '投放渠道',
    start_date   Date COMMENT '开始日期',
    end_date     Date COMMENT '结束日期；可能晚于数据锚点（活动排期）',
    budget_cents Int64 COMMENT '预算（分）'
)
ENGINE = MergeTree
ORDER BY campaign_id
COMMENT '营销活动表，一行一个投放计划';

CREATE TABLE IF NOT EXISTS shop.accounts
(
    account_id  UInt64 COMMENT '订阅账户 ID',
    user_id     UInt64 COMMENT '所属用户 ID',
    plan        LowCardinality(String) COMMENT '套餐；取值 starter/pro/enterprise',
    mrr_cents   Int64 COMMENT '月度经常性收入（分）',
    status      LowCardinality(String) COMMENT '订阅状态；active/paused/churned；MRR 只统计 active',
    started_at  DateTime('Asia/Shanghai') COMMENT '订阅开始时间（Asia/Shanghai）',
    churned_at  Nullable(DateTime) COMMENT '流失时间；非空表示已流失'
)
ENGINE = MergeTree
ORDER BY account_id
COMMENT '订阅账户表，一行一个订阅；用于 MRR 与流失';

CREATE TABLE IF NOT EXISTS shop.meta_data_anchor
(
    anchor_date  Date COMMENT '数据截止日期（通常为昨天）',
    seed         UInt64 COMMENT '生成用随机种子',
    generated_at DateTime COMMENT '生成时间'
)
ENGINE = MergeTree
ORDER BY anchor_date
COMMENT '数据锚点表，单行；预检据此判断数据是否过期';
```

> 用 `Int64` 存“分”，不用 Float，避免浮点误差污染结果比较。展示层统一 `/100.0`。
>
> **所有表和字段都带 `COMMENT`**（12 表 / 77 字段，2026-10-24）：Stage 1 的 schema 目录直接读
> `system.tables.comment` 与 `system.columns.comment`，不再依赖外部注释文件。
> 结构变更后必须用 `seed --schema-only` 重建——`CREATE TABLE IF NOT EXISTS` 不会更新已有表的注释。

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
- **时间戳必须封顶**：`orders` / `payments` / `refunds` / `sessions` / `events` 的所有时间字段 `clamp` 到 `anchor 23:59:59`；`users.deleted_at`、`accounts.churned_at` 若晚于锚点则置 `NULL`。
  否则会出现“未来数据”（例如退款日期到 anchor+20 天、事件溢出到次日），`now()` 窗口会把它们算进来，评测结果与锚点语义不一致。
  该修正于 2026-10-23 落地，**尚未重新 seed**。

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
      WHERE created_at >= toStartOfDay(now()) - INTERVAL 30 DAY
        AND created_at <  toStartOfDay(now())
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

### 6.3 完整样例

> **以 `analytics_agent/evaluation/datasets/v1_30.yaml` 为准**：30 条用例已全部审核，包含
> B-02 的退款按金额占比分摊、C-03/C-15 的上月同期 `least` 封顶、以及“最近 N 天 = 自然日”等修正。
> 本文不再维护重复副本，避免文档与 YAML 漂移。

字段示例（仅示意结构，实际以 YAML 为准）：

```yaml
- id: "A-01"
  user_id: "analyst"
  user_groups: ["analyst"]
  message: "最近 30 天各渠道的 GMV 是多少？"
  metadata:
    layer: A
    golden_sql: |
      SELECT channel, SUM(amount_cents) / 100.0 AS gmv
      FROM shop.orders
      WHERE created_at >= toStartOfDay(now()) - INTERVAL 30 DAY
        AND created_at <  toStartOfDay(now())
        AND status IN ('paid','shipped','completed')
        AND currency = 'CNY' AND is_test = 0
      GROUP BY channel
      ORDER BY gmv DESC
    expected_behavior: answer
```

### 6.4 出题纪律

1. 问题用真实业务口语，不要写“请写出 SQL 查询…”。
2. 同一指标至少两种问法（测检索/口径稳定性）。
3. 不要出“golden 有多种合理答案”的题；若口径确实不唯一，归入 E 类（应澄清）。
4. F 类必须是**数据上真的无法回答**，而不是“模型不会”。
5. 每道题的 golden 结果必须你本人看过，确认“这就是我要的数字”。
6. **多表聚合先确认粒度**：订单级字段（退款、支付）不能直接 JOIN 到商品行后 `SUM`；先聚合到同粒度，或按金额占比分摊。B-02 就是反例（已修复）。

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
python -m analytics_agent.evaluation.cli preview --case A-07,C-15   # 审核：看 golden 结果
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
   CREATE USER IF NOT EXISTS readonly_user IDENTIFIED WITH sha256_password BY '<password>';
   GRANT SELECT ON shop.* TO readonly_user;
   ```
   然后把 `CH_USER=readonly_user`、`CH_PASSWORD=<password>` 写回 `.env`。
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

### D2：口径与 golden（1~2 个半天，业务专家主导）

**产出物**

1. `seed/metrics.md` 口径定稿（含 D2-0 的 5 个裁决）。
2. `datasets/v1_30.yaml` 补齐到 **30 条**（现有 6 条 + 新增 24 条）。
3. 其中 **5 条**带 `without_filter_sql`（用于区分 `FILTER_MISSING` 与 `METRIC_WRONG`）。
4. `check --strict` 全 PASS。

#### D2-0 先裁决 5 个口径问题（30 分钟）

这 5 个问题同时是 E 类用例的“标准答案”，必须先定，否则 golden 自己会打架。

| # | 问题 | 建议裁决 | 影响 |
|---|---|---|---|
| 1 | 复购率的统计窗口 | 近 90 天，按 `orders.created_at`，仅有效订单 | B-05（原 E-05）、E-19 |
| 2 | 渠道归因口径 | 首触：`orders.channel` 即首触渠道 | 所有按渠道的题 |
| 3 | 退款计入哪个月 | 按 `refunds.created_at`（发生月）扣减 | C-15、B-10 |
| 4 | “有效订单”范围 | `paid / shipped / completed`；不含 pending、cancelled、refunded | 所有 GMV/AOV |
| 5 | 混合币种处理 | 经营指标只看 `CNY`；USD 单独出题 | A/B/C 全部 |

> 裁决结果写回 `seed/metrics.md`，并在表格里保留“裁决日期”，后续口径变更走版本记录。

#### D2-1 30 条选题清单（与 `v1_30.yaml` 一一对应）

> 2026-10-22 定稿。`期望` 列：`answer` = 应执行查询并命中结果；`clarify` = 应先澄清；`refuse` = 应说明无法回答/无权限。
> 问题文本与 YAML 中的 `message` 完全一致；改题必须先改 YAML。

| ID | 层 | 用户组 | 问题 | 期望 | 考点 / golden 必须包含 |
|---|---|---|---|---|---|
| A-01 | A | analyst | 最近 30 天各渠道的 GMV 是多少？ | answer | 有效订单 + `CNY` + 排除测试账号；按渠道聚合 |
| A-07 | A | analyst | 近 30 天各支付方式的成功支付金额分别是多少？ | answer | join orders 限 `CNY`/排测试；`status='succeeded' AND is_retry=0`；近 30 自然日 |
| A-08 | A | analyst | 近 7 天各渠道新增用户数是多少？ | answer | `is_test=0`、`deleted_at IS NULL`、相对时间 |
| A-09 | A | analyst | 当前各套餐的 MRR 是多少？（排除测试与注销用户） | answer | 仅 `active`；join users 排测试与注销；不按用户去重 |
| B-02 | B | analyst | 近 30 天各品类的净收入是多少？ | answer | 订单 cohort；approved 退款**按商品金额占比分摊**到 items，禁止直接 SUM 订单级退款 |
| B-10 | B | analyst | 近 90 天各渠道的退款金额是多少？ | answer | 退款 approved；币种/测试过滤；按订单渠道归因 |
| B-11 | B | analyst | 近 30 天销量最高的 10 个商品是哪些？ | answer | items join products；仅有效订单；并列按 `product_id` 升序；`LIMIT 10` |
| B-12 | B | analyst | 近 30 天各国家用户的支付转化率是多少？ | answer | sessions join users；排测试/软删；输出百分比；近 30 自然日 |
| B-13 | B | analyst | 近 30 天各品类的客单价（该品类收入 ÷ 含该品类商品的订单数）是多少？ | answer | 品类收入 / `count(DISTINCT order_id)`；金额 `/100`；aov 取 2 位 |
| C-03 | C | analyst | 本月至今的 GMV，跟上月同期比增长了多少？（截至今天 0 点，上月按相同自然日天数对齐） | answer | 本月止于今天 0 点；上月用 `dateDiff` 天数 + `least` 封顶 |
| C-14 | C | analyst | 近 4 周每周的有效订单数（仅 CNY）是多少？ | answer | 周一为周起点（`toStartOfWeek(..., 1)`）；4 个完整周（不含本周）；仅 CNY |
| C-15 | C | analyst | 本月至今的净收入，跟上月同期比增长多少？（截至今天 0 点，上月按相同自然日天数对齐） | answer | GMV − approved 退款；本月止于今天 0 点；上月同期用 `dateDiff` 天数 + `least` 封顶 |
| C-16 | C | analyst | 近 12 个完整月（不含本月）每月新增用户数及环比是多少？（首月无环比） | answer | 前 12 个完整月；无数据月份补 0；首月环比为 `NULL` |
| D-04 | D | analyst | 上周各渠道的支付转化率是多少？（排除测试与注销用户） | answer | 周一为周起点；排测试/软删；上周完整窗口；百分比输出 |
| D-17 | D | analyst | 近 3 个完整月（不含本月）每月注册用户的 7 日留存率（注册次日起 7 个自然日内有会话）是多少？ | answer | 月度 cohort；D+1~D+7 自然日（`< D+8`）；排除测试/软删；仅前 3 完整月 |
| D-18 | D | analyst | 近 7 天加购后最终下单的会话比例是多少？（同一会话内，purchase 晚于 add_to_cart；排除测试与注销用户） | answer | 分母有加购的会话；分子同会话购买时间晚于加购；join users 排测试/软删 |
| B-05（原 E-05） | B | analyst | 看一下近 90 天复购率 | answer | 近 90 天 ≥2 笔有效订单的用户占比；多表 join |
| E-19 | E | analyst | 看一下销售额 | clarify | 未给指标（GMV/净收入）、时间、渠道 |
| E-20 | E | analyst | 算下留存 | clarify | 新用户留存还是活跃留存、窗口未知 |
| E-21 | E | analyst | 最近的转化率怎么样 | clarify | 渠道/设备/时间均未指定 |
| F-06 | F | analyst | 帮我查一下竞品的市场份额 | refuse | 库内无竞品数据，应明确说明不可回答 |
| F-22 | F | analyst | 查一下我们的库存周转天数 | refuse | 库内无库存表，应明确说明不可回答 |
| G-23 | G | viewer | 各渠道 GMV 是多少？ | refuse | 无 `run_sql` 权限（`access_groups` 闸门） |
| G-24 | G | viewer | 把最近 10 条订单明细列出来 | refuse | 同上；不得编造数据 |
| G-25 | G | guest（空组） | 本月订单量是多少？ | refuse | 默认拒绝（fail-closed） |
| H-26 | H | analyst | 近 7 天每天的 purchase 事件数是多少？ | answer | `events` 时间过滤；`event_name='purchase'`；7 个完整自然日 |
| H-27 | H | analyst | 近 30 天各类事件的次数分布是怎样的？ | answer | 100 万行 + 时间过滤；验证分区裁剪 |
| I-28 | I | analyst | 近 30 天各渠道的 GMV 是多少？ | answer | `conversation_id=multi-1` 第 1 轮，建立上下文 |
| I-29 | I | analyst | 只看 organic 呢？ | answer | 第 2 轮；继承上一轮时间窗口与口径 |
| I-30 | I | analyst | 跟再往前 30 天比呢？ | answer | 第 3 轮；继承 organic；滚动 30 天 vs 前 30 天 |

**G 类说明**：本库没有行级安全（RLS），G 只验证**工具级闸门**（`access_groups`）。行级安全在 Stage 5 用 `transform_args` 实现，届时再扩 G 类。

**I 类说明**：3 条共用一个 `conversation_id`，按 I-28 → I-29 → I-30 顺序执行；依赖 `--concurrency 1`（默认值）保证顺序。

#### D2-2 单条用例的标准流程（每条 8~12 分钟）

0. **预览**（审核阶段用）：逐条打印问题、期望与 golden 结果，避免在客户端里反复贴 SQL。
   ```bash
   python -m analytics_agent.evaluation.cli preview --case A-07,C-15
   python -m analytics_agent.evaluation.cli preview            # 全部 30 条
   ```
1. **选题**：从 D2-1 表里取一条，先写问题文本（真实口语，不要提示 SQL 写法）。
2. **写 golden SQL**：按 `metrics.md` 口径；只读账号执行。
3. **核对结果**：
   - 行数是否符合常识（渠道 4、状态 6、品类 5、国家 4）；
   - 量级是否合理（GMV 百万级、转化率 0~1）；
   - 抽 1 行手工复算（用明细 SQL 验证）。
4. **控制结果规模**：返回行数 > 50 的题加聚合或 `LIMIT`，否则断言脆弱且难人工核对。
5. **补 metadata**：`layer`、`expected_behavior`、必要时 `order_sensitive` / `column_alias` / `without_filter_sql`。
6. **写入 `v1_30.yaml`**：保持 id 升序，`golden_sql` 用 `|` 块。
7. **立刻自检**：
   ```bash
   python -m analytics_agent.evaluation.cli check --dataset analytics_agent/evaluation/datasets/v1_30.yaml
   ```
   该条 `golden.<id>` 必须 PASS（非空、<5s），再写下一条。

#### D2-3 完成标准（DoD）

- [ ] `check --strict --dataset v1_30.yaml` 全 PASS（30 条，且用例数 ≥ 30）
- [ ] 每条 golden SQL 非空、单次 < 5s
- [ ] 至少 5 条带 `without_filter_sql`
- [ ] 30 条覆盖 A 4 / B 5 / C 4 / D 3 / E 4 / F 2 / G 3 / H 2 / I 3
- [ ] 抽查 5 条做“破坏性验证”：去掉一个过滤条件后结果必须变化（防止恒真用例）
- [ ] 每条都能用一句话解释数字来源
- [ ] 提交 YAML 与 `metrics.md`，并在附录 D 追加记录

#### D2-4 时间与风险

| 事项 | 预估 |
|---|---|
| D2-0 口径裁决 | 30 分钟 |
| 24 条用例（含核对） | 3.5~5 小时，建议分 2 段 |
| 校验与返工 | 1 小时 |

常见坑：

- 不要写死日期（数据锚点是“昨天”，每天会滚动）；
- 不要依赖随机数据的具体数值（只依赖渠道/品类/状态等固定枚举）；
- 金额字段是“分”，展示必须 `/100.0`；
- 时间窗口与锚点对齐：数据只到昨天，`now()` 之后的当天数据为空是正常的；
- 多轮用例必须 `--concurrency 1` 且共享 `conversation_id`。

### D3：evaluator 与 runner（已完成）

代码已完成并通过单测（提交 `1e1e565` 起）。当前实现见 `analytics_agent/evaluation/{normalize,compare,evaluators,cli}.py`；
与早期设计的两处差异：列比较支持 `column_match="values"`，执行统一走 `execute_safe()`。

**DoD**（已满足）：`smoke.yaml` 能产出 `report.json` / `report.html` / `failures.csv`，失败用例带 `executed_sql`。

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

> 当前状态：**已记录（估算基线）**。2026-10-23 用 DeepSeek 跑过 30 条；跑后修正了评分规则（列投影、自然日窗口），按裁定不重跑。
> 实测 EX 为 0.0%（0/21），95% 置信上界约 13.6%；**保守记为 15%**，作为 Stage 1 的起点。

| 指标 | 数值 | 备注 |
|---|---|---|
| 数据集版本 | `shop_v1_30` | 30 条，A~I 已审核 |
| 模型 | `deepseek-flash` | `LLM_PROVIDER=deepseek` |
| 配置 | `temperature=0.0, max_tool_iterations=8` | 精简工具（run_sql + visualize） |
| EX | **0.0%**（0/21） | 95% 上界 ≈13.6%；**记录为 15%**（保守估计） |
| EX（复跑） | 未测 | 评分规则修正后未重跑 |
| EX（首答命中） | 0.0% | 首次 `run_sql` 即命中 |
| 平均工具轮次 | **9.83** | 18/30 触达 8 轮上限 |
| P95 延迟 | **31,406 ms** | |
| 平均 token/题 | ≈8,408（估算） | prompt 字符数 / 4，非 API usage |
| 失败分布 | `SHAPE_MISMATCH` 21 / `AMBIGUOUS` 5 | `UNKNOWN` = 0% |

### 基线观察（30 条实跑，2026-10-23）

1. **无 schema 时探索失控**：18/30 触达 8 轮上限，平均 9.83 次工具调用；模型反复查元数据、换聚合方式，最后常常没算完就耗尽轮次。这是 Stage 1 最直接的收益点。
2. **真实口径错误可定位**：`A-07` 漏 `is_retry=0`（74.7M vs 72.6M）、`C-14` 自行剔除“已批准退款订单”、`A-01` 输出占比而非 GMV。这些即使修好评分规则也不会通过，是有效失败。
3. **行为层两极分化**：F 类 2/2 通过（能明确说“数据不存在”），G 类 2/3（空组用户仍编答案），E 类 0/4（全部自定口径直接查）。Stage 1 需要显式写入“先澄清、无权限不答”的规则。

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
| 1:N join 后直接 SUM 订单级字段 | 退款/金额被放大 N 倍并错分到多个品类 | 先聚合到同一粒度，或按金额占比分摊（见 B-02、`metrics.md` 第 6 条） |

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
| 2026-10-22 | D1-0 | 创建只读账号并授权 | 成功 | 授权范围保持现状（2026-10-23 裁定，无需收紧） |
| 2026-10-22 | D1-2 | `seed --scale 0.1` | 成功 | 12 表 312,569 行；明细见下 |
| 2026-10-22 | D1-2 | 只读账号抽查（锚点/测试账号/币种/状态/重试/GMV） | 通过 | 锚点 `2026-10-21`，seed=42 |
| 2026-10-22 | D1-4 | `check --dataset smoke.yaml` | **20/20 PASS** | 只读探针生效；golden `A-01` 4 行 / 0.02s |
| 2026-10-22 | D1-0 | 模型选择与采样参数 | 选定 `qwen2.5-coder:7b`，`temperature=0.0`、`num_ctx=8192` | 修复 Ollama 忽略 `LlmRequest.temperature` 的问题；`qwen2.5:7b` 留作对照 |
| 2026-10-22 15:05 | D1-5 | 冒烟首跑（3 条，含记忆工具） | 失败：`A-01` 超时 324s | 5 次工具调用后 `ReadTimeout`；`/api/ps` 显示 `size_vram=0`（纯 CPU） |
| 2026-10-22 15:08 | D1-5 | 精简工具 + 放宽超时 + 限生成 | 3 条：11~26s | 去掉记忆工具（少一轮“强制检索”）、`num_predict=512`、超时 600s、迭代上限 5 |
| 2026-10-22 15:11 | D1-5 | 新增 `A-02`（显式表名/字段） | `A-02` 首次走通 `run_sql` | 模型执行 `SELECT status, COUNT(*) ...`，仅列别名不同被判错 |
| 2026-10-22 15:13 | D1-5 | 列值匹配 + 收紧拒答判定 | `A-02` PASS | 新增 `column_match="values"`；`F-06` 反问用户不再算拒答 |
| 2026-10-22 15:15 | D1-5 | 归因口径与评测器对齐 | 最终 4 条：1 PASS / 3 FAIL | EX 50%、首答 50%、behavior 0%、avg_tools 0.5 |
| 2026-10-22 | D2-0 | 细化 24 条选题、流程与验收 | 完成（文档） | 见 D2 章节与附录 E 工作表 |
| 2026-10-22 | D2-0 | 5 项口径裁决 | 已确认 | 复购率 90 天 / 首触归因 / 退款按发生月 / 有效订单三态 / 仅 CNY |
| 2026-10-22 | D2-1 | 产出 24 条用例并合并进 `v1_30.yaml` | 完成 | 21 条 golden 全部可执行；`C-16` 改写为自连接避免窗口类型问题 |
| 2026-10-22 | D2-1 | `check --strict --dataset v1_30.yaml` | **41/41 PASS** | 30 条用例；golden 均 <0.1s；5 条 without_filter 结果均变化 |
| 2026-10-22 | D2-3 | 新增 DeepSeek API 适配（OpenAI 兼容 + temperature 透传） | 代码完成，待配置 Key 后冒烟 | 切换步骤见附录 F；本地 Ollama 仍可回退 |
| 2026-10-22 | D2-3 | 重构：DeepSeek 移入 `vanna.integrations.deepseek`，OpenAI 增加可选 `temperature` | 完成 | 删除 `analytics_agent/llm/`；模型名不再白名单；相关单测 40 passed |
| 2026-10-23 | D1-2 | 跨天后重新 `seed --scale 0.1` | 成功 | 锚点 `2026-10-22`；12 表行数不变；`check --strict` 41/41 |
| 2026-10-23 | D2-2 | 新增 `preview` 子命令 | 完成 | 支持 `--case` 过滤、`--rows` 控制行数；用于逐条审核 |
| 2026-10-23 | D2-2 | B-02 审核发现退款重复计算（1:N join 放大） | 已修复 | 改为按商品金额占比分摊；校验 `16,800,867.37 − 67,056.56 = 16,733,810.81` 与分品类求和一致 |
| 2026-10-23 | D2-2 | B-02 修复后复核 | **通过** | 确认退款分摊规则；订单 cohort（B-02）与退款发生月（C-15）两套口径并存已确认 |
| 2026-10-23 | D2-2 | C-14 周起点改为周一 | 已调整 | `toStartOfWeek(..., 1)`（含 WHERE 边界）；4 行：09-21/09-28/10-05/10-12 |
| 2026-10-23 | D2-2 | C-15 改写为 CTE + `ifNull`/`nullIf` + 显式上界 | 已调整 | 结果 `1.57%` 与旧写法一致；`check --strict` 41/41 |
| 2026-10-23 | D2-2 | C-03 / C-15 的 3 处“上月同期”上界加 `least` 封顶 | 已加固 | 月末验证：`2026-12-31` 场景 raw=`12-02` → capped=`12-01`；结果不变 |
| 2026-10-23 | D2-2 | E / F / G 三层审核（4+2+3） | **通过** | E 确需澄清；F 确需拒答；G 应回答无权限 |
| 2026-10-23 | D2-2 | H-26 改为“最近 7 个完整自然日” | 已调整 | `toStartOfDay(now())` 左闭右开；7 行（10-16~10-22） |
| 2026-10-23 | 生成器 | 事实表时间戳 clamp 到锚点 23:59:59 | 已修改 | 修复 events 溢出到 10-23 |
| 2026-10-23 | A1 | 重新 seed（时间戳封顶生效） | 成功 | 行数不变；锚点 `2026-10-22` |
| 2026-10-23 | A1 | 事实表时间戳上界校验 | 通过 | orders/payments/refunds/sessions/events 最大时间均为 `2026-10-22 23:59:59` |
| 2026-10-23 | A2 | `check --strict --dataset v1_30.yaml` | **41/41 PASS** | 重灌后 golden 全部可执行 |
| 2026-10-23 | B1 | DeepSeek 冒烟（4 条） | 完成 | EX 50%、首答 50%、behavior 0%、avg_tools 5.5；`A-02` 1 次调用命中 |
| 2026-10-23 | B1 | 观察 | — | A-01 自检后未出 GMV（5 轮上限）；E-05 自定口径未澄清；F-06 查元数据后拒答被判失败 |
| 2026-10-23 | B1-1 | F 类判定：允许元数据查询，仍要求拒答措辞 | 已修改 | `REFUSE_CUES` 增加“暂无/无数据”；`labeling` 同步；25 passed |
| 2026-10-23 | B1-2 | 迭代上限 5 → 8 | 已修改 | `.env` / `config.py` / `.env.example` 统一为 8 |
| 2026-10-23 | B2 | 首次 30 条运行 | 已跑，**非基线** | EX 0%、首答 0%、behavior 44.4%、avg_tools 9.83；18 条触达 8 轮上限 |
| 2026-10-23 | B2 | 失败归因抽查 | 发现两处评测规则问题 | ① 严格列数导致多列答案全判失败；② “近 N 天”滚动 vs 自然日口径不一致 |
| 2026-10-23 | B2-1 | 列投影评分（golden 列 ⊆ actual） | 已修改 | 默认 `column_match="projection"`；按值签名兼容列别名；新增单测 |
| 2026-10-23 | B2-2 | “近 N 天”改自然日窗口 | 已修改 | 9 条 golden；`metrics.md` 第 10 条；`check --strict` 41/41 |
| 2026-10-23 | B2-3 | 记录估算基线 | 已记录 | 第 9 节：实测 0/21，95% 上界 13.6%，保守记 15%；未重跑 |
| 2026-10-24 | T0-0 | DDL 全量补 `COMMENT`（表 + 字段） | 完成 | 12 表 / 77 字段 100% 有注释 |
| 2026-10-24 | T0-0 | 新增 `seed --schema-only`（强制 drop） | 完成 | 只重建结构、不灌数；`CREATE IF NOT EXISTS` 不会更新 COMMENT |
| 2026-10-24 | T0-0 | 重建表结构（`--schema-only`） | 成功 | `orders`/`anchor` 均为 0 行；注释已写入 `system.*` |
| 2026-10-27 | 数据 | 重灌至锚点 `2026-10-26` | 成功 | 12 表；`check --strict` 42/42 |
| 2026-10-27 | D-17 | 修正 7 日留存 golden | 完成 | 自然日 D+1~D+7；仅前 3 完整月；3 行结果 |
| 2026-10-23 | D2-2 | I 类审核 | **通过** | 多轮设计合理；待后续实际运行验证 |
| 2026-10-25 | 报表 | HTML 报告取消 Executed SQL 400 字截断 | 完成 | 现有报告最长 SQL 1,753 chars 完整显示；CSS 自动换行 |

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

**2026-10-22 · D1-5 冒烟结果（4 条，`qwen2.5-coder:7b`）**

| 用例 | 结果 | 分类 | 工具调用 | 耗时 |
|---|---|---|---|---|
| A-01 各渠道 GMV（未给表名） | FAIL | `NO_TOOL` | 0 | 10.5s |
| A-02 按 status 统计订单数（显式表名） | **PASS** | `OK` | 2（`run_sql` + `visualize_data`） | 39.6s |
| E-05 复购率（口径不明确） | FAIL | `AMBIGUOUS` | 0 | 24.9s |
| F-06 竞品市场份额（库内无数据） | FAIL | `AMBIGUOUS` | 0 | 10.5s |

汇总：EX 50%、首答命中 50%、behavior 0%、平均工具轮次 0.5、P95 39.6s。

**性能事实（决定后续调参）**

- Ollama `/api/ps` 显示 `size_vram = 0`：模型**纯 CPU 推理**，约 10 tok/s 生成、45 tok/s prompt 预处理。
- 同一请求 5 次工具调用即可超过 240s 默认超时，因此必须限制生成长度与迭代次数。
- `A-02` 在问句里显式给出 `shop.orders`/`status` 后，模型能正确调用工具——说明工具链路本身可用。

**三条根因（直接作为 Stage 1 的输入）**

1. **没有 schema/方言上下文**：`A-01` 模型把 SQL 当作文本输出（`sales_data`、`CURDATE()`），而不是调用工具；即使调用也会猜错表名。→ Stage 1 补 L1 方言 + L2 表清单。
2. **没有澄清策略**：`E-05` 直接编了一段 SQL 回答，而不是询问时间范围/口径。→ prompt 增加“口径不明确先澄清”。
3. **没有拒答策略**：`F-06` 反问用户“请提供竞品表”，既没查库也没说明数据不存在。→ prompt 增加“库内无数据时明确说明不可回答”。

**本轮顺带修复**

- `column_match="values"`：列别名不同（`cnt` vs `order_count`）不再误判；列值集合无法配对时才判 `columns` 失败。
- 拒答判定增加 `REFUSE_CUES`：仅反问用户不算拒答；`labeling` 与 evaluator 口径统一。
- 记忆工具默认关闭（`enable_memory_tools=False`），避免“强制先检索”多一轮 LLM 调用。
- Ollama 增加 `LLM_TIMEOUT_S` / `LLM_MAX_PREDICT` / `AGENT_MAX_ITERATIONS` 配置。

### E. D2 工作表（30 条，逐条勾选）

> 用法：`SQL` 列打勾表示已写并在只读账号执行通过；`核对` 表示人工看过行数与量级；
> `入 YAML` 表示已合并进 `v1_30.yaml` 且该条 `check` 通过。

| ID | 层 | 期望 | SQL | 核对 | 入 YAML | 审核意见 |
|---|---|---|---|---|---|---|
| A-01 | A | answer | [x] | [x] | [x] | |
| A-07 | A | answer | [x] | [x] | [x] | 补 30 天窗口 + join orders 限 CNY/排测试（2026-10-25） |
| A-08 | A | answer | [x] | [x] | [x] | |
| A-09 | A | answer | [x] | [x] | [x] | 补 join users 排测试与注销（2026-10-27） |
| B-02 | B | answer | [x] | [x] | [x] | 题面明确“近 30 天”；退款分摊口径已确认（2026-10-27） |
| B-10 | B | answer | [x] | [x] | [x] | |
| B-11 | B | answer | [x] | [x] | [x] | 补 30 天窗口 + 并列按 `product_id` 升序（2026-10-25） |
| B-12 | B | answer | [x] | [x] | [x] | 补 30 天窗口 + 排软删，输出百分比（2026-10-25） |
| B-13 | B | answer | [x] | [x] | [x] | 题面明确口径；aov 取 2 位小数（2026-10-25） |
| C-03 | C | answer | [x] | [x] | [x] | 本月止于今天 0 点；上月按 `dateDiff` 对齐（2026-10-27） |
| C-14 | C | answer | [x] | [x] | [x] | 周起点周一；题面明确仅 CNY（2026-10-27） |
| C-15 | C | answer | [x] | [x] | [x] | 本月止于今天 0 点；上月同期按 `dateDiff` 对齐 + `least` 封顶（2026-10-27） |
| C-16 | C | answer | [x] | [x] | [x] | 前 12 个完整月（不含本月）；首月环比 `NULL`（2026-10-27） |
| D-04 | D | answer | [x] | [x] | [x] | 周起点周一 + 排测试/软删；百分比输出；去掉 `sessions` 诊断列（2026-10-27） |
| D-17 | D | answer | [x] | [x] | [x] | 自然日 D+1~D+7、前 3 个完整月；去掉 `retained_users` 诊断列（2026-10-27） |
| D-18 | D | answer | [x] | [x] | [x] | 同会话漏斗；补 users 排测试/注销（2026-10-27） |
| B-05（原 E-05） | B | answer | [x] | [x] | [x] | 改为 answer 后归入 B 类：近 90 天复购率（2026-10-27） |
| E-19 | E | clarify | — | [x] | [x] | 通过：缺指标/时间/渠道 |
| E-20 | E | clarify | — | [x] | [x] | 通过：留存类型与窗口未定 |
| E-21 | E | clarify | — | [x] | [x] | 通过：渠道/设备/时间未定 |
| F-06 | F | refuse | — | [x] | [x] | 通过：库内无竞品数据；允许先查元数据，最终须明确说明不可回答 |
| F-22 | F | refuse | — | [x] | [x] | 通过：库内无库存表 |
| G-23 | G | refuse | — | [x] | [x] | 通过：viewer 无 `run_sql`，应回答无权限 |
| G-24 | G | refuse | — | [x] | [x] | 通过：同上 |
| G-25 | G | refuse | — | [x] | [x] | 通过：空组默认拒绝 |
| H-26 | H | answer | [x] | [x] | [x] | 已改为最近 7 个完整自然日，问题明确为 `purchase`（2026-10-25） |
| H-27 | H | answer | [x] | [x] | [x] | 通过：4 类事件，近 30 天 |
| I-28 | I | answer | [x] | [x] | [x] | 通过：多轮设计合理（待实际运行验证） |
| I-29 | I | answer | [x] | [x] | [x] | 通过：自然承接上一轮 |
| I-30 | I | answer | [x] | [x] | [x] | 通过：继承 organic + 滚动 30 天对比 |

> `核对`：业务负责人逐条确认数值量级与口径后打 `x`。
> `审核意见`：写“通过”，或“需调整：<具体原因>”；有调整时同步改 `v1_30.yaml` 并重跑 `check`。

### F. 切换到 DeepSeek API（替代本地 Ollama）

**为什么换**：本地 `qwen2.5-coder:7b` 是纯 CPU 推理（`size_vram=0`），约 10 tok/s；
30 条基线预计 15~25 分钟，且长工具结果容易触发超时。DeepSeek 走 OpenAI 兼容接口，
速度快、工具调用稳定，30 条基线通常只要几分钟。

**步骤**

1. 安装依赖（当前 venv 若未有 `openai`）：
   ```bash
   pip install "vanna[openai]"
   ```
2. 改 `.env` 的 4 行：
   ```ini
   LLM_PROVIDER=deepseek
   LLM_MODEL=deepseek-chat
   DEEPSEEK_API_KEY=sk-***
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   ```

   > **最少需要 3 项**：`LLM_PROVIDER`、`LLM_MODEL`、`DEEPSEEK_API_KEY`。
   > `DEEPSEEK_BASE_URL` 有默认值（`https://api.deepseek.com`），可以省略；
   > 只配 `DEEPSEEK_API_KEY` 不够——默认 provider 仍是 `ollama`。
3. 验证配置（不调用 LLM，只打印生效参数）：
   ```bash
   python -c "import dataclasses; from analytics_agent.config import Settings; from analytics_agent.agent_factory import build_llm_service; s=Settings.from_env(); svc=build_llm_service(s); print(type(svc).__name__, svc.model, svc._client.base_url)"
   ```
4. 冒烟验证工具调用（先 4 条，别直接跑 30 条）：
   ```bash
   python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/smoke.yaml
   ```

**为什么用 `deepseek-chat` 而不是 `deepseek-reasoner`**：Agent 依赖 function calling
（`run_sql` 等），`deepseek-chat` 明确支持；`deepseek-reasoner` 在部分版本/场景下对工具调用
支持不完整，先用 chat 跑通基线，再考虑推理模型。

**实现说明**：DeepSeek 集成位于框架侧 `src/vanna/integrations/deepseek/`，与 openai / anthropic 同级：

- `llm.py` 复用 `OpenAILlmService`，默认 `base_url=https://api.deepseek.com`、`temperature=0.0`；
- **模型名不做白名单**，`deepseek-flash` 等自定义 ID 可直接填；只拦截含 `:` 的 Ollama 标签（如 `qwen2.5:7b`），避免配置串台；
- `DEEPSEEK_API_KEY` 为空时启动即报错；也可从环境变量自动读取；
- 框架 `OpenAILlmService` 新增可选构造参数 `temperature`（`None` 表示不发送），
  因此不再需要覆盖私有方法 `_build_payload()`，框架升级不会失效。

配套改动：`pyproject.toml` 新增 extra `deepseek = ["openai"]`；业务侧 `analytics_agent/config.py`
只保留 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` 两个配置项与工厂分支。

**注意**

- Key 只放 `.env`（已被 `.gitignore` 忽略），不要写进代码或提交。
- 公司网络若走代理，需要让 `api.deepseek.com` 直连或配置 `HTTPS_PROXY` / `NO_PROXY`。
- 切回本地模型只需把 `LLM_PROVIDER` 改回 `ollama`、`LLM_MODEL` 改回本地模型名，无需改代码。
- 成本：30 条基线通常是“几分钱”量级；跑全量前先看一次 `report.json` 里的 token 估算。
