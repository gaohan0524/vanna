# Vanna 2.0 Text2SQL Agent 质量提升开发计划

- **状态**：进行中（本文档为执行基线，随迭代更新）
- **当前进度**：**Stage 1 进行中**——T1-1 ~ T1-4 已完成（2026-10-25），下一步 T1-5 接线（把 `AnalyticsPromptBuilder` 接进 `build_agent`）。计划见 [`STAGE1-CONTEXT.md`](./STAGE1-CONTEXT.md) 第 12 节；Stage 0 详见 [`STAGE0-EVAL.md`](./STAGE0-EVAL.md) 0.1
- **适用范围**：基于 Vanna 2.0 自建的 Text2SQL / 数据分析 Agent（当前基线见 `src/vanna/examples/test.py`：ClickHouse + 本地 LLM + DemoAgentMemory）
- **前提假设**：模型能力不是瓶颈（使用当前最强模型）。因此本文的优化重点全部放在**上下文供给、工具设计、检索质量、评测闭环**上。
- **最后更新**：2026-10-21

---

## 0. 如何看这份文档

1. 第 2 章定义「什么叫质量好」，所有后续改动都必须能落到这里的指标上。
2. 第 3 章说明每一类知识/能力在 Vanna 2.0 中应该放在哪个扩展点，**避免把东西塞错地方**。
3. 第 5 章是执行主体，按 Stage 0~5 推进，每个 Stage 有独立验收标准，未达标不进下一阶段。
4. 第 9 章是勾选式进度表，开发时直接改这里。

---

## 1. 背景与目标

### 1.1 现状问题（按影响排序）

| # | 问题 | 表现 | 根因 |
|---|---|---|---|
| 1 | 无 schema / 方言上下文 | 表名、字段名靠猜；ClickHouse 语法写错 | 未配置 `SystemPromptBuilder`，也没有 schema 工具 |
| 2 | 无指标口径 | 同一个「GMV」每次算法不同 | 无语义层，口径只存在于人的脑子里 |
| 3 | 记忆后端不适用中文 | 检索几乎检不到，白跑一轮工具 | `DemoAgentMemory` 按空格分词 + 阈值 0.7 |
| 4 | 工具面过窄 | 只能写 SQL，不能探查字段/取值 | 只有 `run_sql` + `visualize_data` |
| 5 | 无评测 | 改动无法验证，只能靠感觉 | 未接入 `core/evaluation` |
| 6 | 采样温度偏高 | SQL 不稳定、字段幻觉 | `AgentConfig.temperature` 默认 0.7 |

### 1.2 目标

**北极星指标**：执行准确率（Execution Accuracy, EX）——在评测集上，Agent 最终执行的 SQL 返回的结果集与 golden 结果集一致的比例。

分阶段目标（假设评测集为 100 条分层用例）：

| 阶段 | EX | 其他关键指标 |
|---|---|---|
| 基线 | ~40-50% | 无观测 |
| Stage 1 后 | +15pt | 字段不存在类错误 < 5% |
| Stage 2 后 | +8pt | 平均工具轮次 ≤ 3.5 |
| Stage 3 后 | ≥ 80% | 检索 recall@5 ≥ 0.9 |
| Stage 4 后 | ≥ 88% | 澄清准确率 ≥ 85%，多轮成功率 ≥ 75% |
| Stage 5 | 持续收敛 | 每周 top 失败模式下降 |

### 1.3 非目标

- 不改造框架本身（除非扩展点确实缺失，走 `docs/optimizations/` 单独记录）。
- 不做模型微调。
- 不追求 100% 覆盖长尾问题；优先保证高频问题的稳定性与可解释性。

---

## 2. 质量模型

### 2.1 三层指标

```
结果层  EX / 口径正确率 / 拒答正确率
过程层  工具轮次 / 检索命中率 / 自我修复成功率 / 澄清准确率
系统层  P95 延迟 / 单问题 token / 失败率 / 成本
```

**只看结果层会掩盖问题**：EX 不变但工具轮次翻倍，说明走对了但效率退化，必须同时看过程层。

### 2.2 指标定义

| 指标 | 定义 | 采集方式 |
|---|---|---|
| EX | 归一化后结果集与 golden 一致（列名不敏感、行序无关、浮点相对误差 1e-6） | 自定义 `ResultSetEvaluator` |
| 口径正确率 | LLM-as-judge 判断答案是否使用了正确业务口径 | `LLMAsJudgeEvaluator` |
| 工具轮次 | `AgentResult.tool_calls` 长度 | `EfficiencyEvaluator` |
| 检索 recall@k | golden 依赖的表/字段/口径是否出现在 top-k 上下文 | 离线脚本 |
| 自我修复成功率 | 首次 SQL 失败但最终成功 | 轨迹分析 |
| 澄清准确率 | 「应澄清」用例真的先问；「不应澄清」用例没有多余提问 | 分层用例 |
| P95 延迟 | 端到端 | 观测平台 |

### 2.3 失败分类法（taxonomy）

所有失败必须归入以下之一，**每周按类统计**，否则无法定位：

| code | 含义 | 典型修复 |
|---|---|---|
| `SCHEMA_MISSING` | 表/字段不存在或幻觉 | 补 schema 目录、`search_columns` |
| `DIALECT_ERROR` | 方言函数错误 | 方言规则库 + `EXPLAIN` |
| `METRIC_WRONG` | 口径错误 | 指标工具 / 语义层 |
| `FILTER_MISSING` | 漏了默认过滤（测试账号、软删、时区） | prompt 硬约束 + 行级模板 |
| `AMBIGUOUS` | 应澄清却直接猜 | 澄清策略 |
| `TIMEOUT` | 查询超时/扫全表 | 行级上限 + `EXPLAIN` 前置 |
| `TOOL_MISUSE` | 工具参数错、无意义往返 | 参数收敛、description 改写 |
| `RENDER_WRONG` | 结果对但呈现错（图表/单位） | 呈现规则 |

---

## 3. 总体思路与架构落点

### 3.1 五层上下文（按“是否需要每次都在”划分）

| 层 | 内容 | 放哪 | 更新频率 |
|---|---|---|---|
| L0 稳定指令 | 角色、方言、硬约束、行为规则、输出格式 | `SystemPromptBuilder` 子类 | 周级 |
| L1 常驻 schema | 库名、表清单、字段与枚举、命名约定 | `SystemPromptBuilder`（启动时从 catalog 加载） | DDL 变更时 |
| L2 指标与术语 | 指标名、公式、同义词、易错点 | `SystemPromptBuilder`（`glossary.yaml`） | 周级 |
| L3 按需事实 | 字段级探查、样例数据、golden SQL | 工具 / `AgentMemory` | 实时 |
| L4 运行时状态 | 用户、时区、权限、当前会话选中的表 | `ToolContextEnricher` → `metadata` | 每请求 |

**关键认知**：`ToolContextEnricher` 写入的 `metadata` **不会进入 prompt**（见 `core/enricher/base.py`、`core/agent/agent.py` 中构造 `ToolContext` 的位置），它只供工具读取。要让模型看到，必须走 `SystemPromptBuilder`、`LlmContextEnhancer` 或工具返回值。

### 3.2 扩展点映射表

| 扩展点 | 本项目用途 | 注意事项 |
|---|---|---|
| `system_prompt_builder` | L0 + L1 + L2 上下文 | 每条消息只构建一次；入参只有 `user` 和 `tools`，拿不到请求上下文 |
| `llm_context_enhancer.enhance_system_prompt` | 会话级检索（选候选表、注入相关口径） | 每条消息执行一次 |
| `llm_context_enhancer.enhance_user_messages` | 每步按需注入（不污染历史） | 每次重建 LLM 请求都会执行（含每轮工具迭代） |
| `context_enrichers` | 用户时区、权限、默认库 | 只对工具可见 |
| `tool_registry` 自定义工具 | 探索型工具（schema/枚举/指标） | 工具清单进入循环后固定，运行时新增工具下一条消息才可见 |
| `AgentMemory` | golden Q→SQL、口径文本 | 必须换掉 `DemoAgentMemory`；注意 Chroma 后端不按用户过滤 |
| `lifecycle_hooks` | 高质量轨迹沉淀、配额、审计补充 | `after_tool` 拿得到 `ToolResult` |
| `conversation_filters` | 历史裁剪、工具结果压缩 | 每次 `_build_llm_request` 执行 |
| `error_recovery_strategy` | LLM 瞬时错误退避 | 只对通信错误与未捕获异常生效，业务错误仍回喂模型 |
| `workflow_handler` | `/help`、`/status`、确定性澄清短路 | 现有实现仅 admin 可用部分命令 |
| `observability` / `audit` | 埋点与合规 | 与评测共用同一套 trace 最好 |

### 3.3 上下文预算原则

1. 常驻上下文 ≤ 2000 token；超出部分一律转成“可检索/可调用”。
2. 一次工具返回给 LLM 的文本 ≤ 4000 字符，超出截断并显式标注“已截断”。
3. 同一事实只允许有一个权威来源（例如指标公式只放指标工具，不在 prompt 里重复）。
4. 任何新增 prompt 内容都必须能在评测集上证明收益，否则删除。

---

## 4. 代码结构规划

在仓库根新增**一个**业务包 `analytics_agent/`，评测作为其子包 `analytics_agent.evaluation`（不要改 `src/vanna/` 框架代码；也不要单开根目录 `eval/`——命名与框架 `core/evaluation` 不一致，且过于通用）：

```
analytics_agent/
  __init__.py
  config.py            # 环境变量、连接配置、开关
  agent_factory.py     # 组装 Agent（唯一入口，示例 test.py 收敛到这里）
  prompts/
    builder.py         # L0+L1+L2 SystemPromptBuilder
    templates.py       # 提示词片段（方言、约束、输出格式）
  context/
    schema_catalog.py  # 启动时加载 schema 目录
    enhancer.py        # 候选表检索、口径注入
    enrichers.py       # 用户/时区/权限
  tools/
    search_columns.py
    describe_table.py
    get_metric.py
    sample_rows.py
    explain_query.py
  memory/
    chroma_memory.py   # 或自建混合检索后端
    seeding.py         # 种子导入（幂等）
    retrieval.py       # 混合检索 + rerank
  sql/
    guardrails.py      # AST 级只读校验、LIMIT 注入、超时
    result_norm.py     # 结果集归一化
  evaluation/          # 子包：python -m analytics_agent.evaluation.cli
    cli.py             # check / seed / run / report
    preflight.py       # 锚点、连接、只读、golden 自检
    executor.py        # 只读执行（重放 + golden）
    compare.py         # 结果集等价比较
    evaluators.py      # ResultSet / Clarification / FirstTry
    labeling.py        # 失败分类
    datasets/          # YAML 用例
    seed/              # DDL 与合成数据生成
```

`src/vanna/examples/test.py` 只保留启动入口，业务逻辑全部迁入 `analytics_agent/`，便于测试；将来独立部署时整个目录可直接搬走。

**配套配置**（详见 `STAGE0-EVAL.md` 4.2）：`pyproject.toml` 的 flit sdist 排除 `analytics_agent/`、`scripts/`、`reports/`；`tox.ini` 的 ruff 命令追加 `analytics_agent/`；`.gitignore` 增加 `reports/`。

---

## 5. 阶段计划

### Stage 0：评测与可观测（P0，5 天）

> **详细执行步骤见 [`docs/STAGE0-EVAL.md`](./STAGE0-EVAL.md)**（含合成库 DDL、口径卡片、30 条用例模板、评测代码骨架、D1~D5 每日任务）。本章只保留总体要求。

**前提调整**：当前没有真实业务，主评测集改为**自建 ClickHouse 合成业务库**（仅方案负责人担任业务专家），方法论参考 `ClickHouse/data-agent-mnist`（Apache-2.0）。公开基准（Spider/BIRD）只作为“SQL 生成能力”的对照，不进入主评测。

**目标**：建立可重复、可对比的度量体系。没有这一步，后续所有改动都是盲调。

#### 任务

- **T0-1 评测集（先 30 条，G0 后再扩到 100 条）**
  - Stage 0 分层与配额（30 条）：单表聚合 4、多表 join 5、时间窗口/同环比 4、留存/漏斗 3、指标口径歧义 4（应澄清）、无法回答 2（应拒答）、权限/RLS 3、大表/性能 2、多轮 3。
  - G0 通过后按原配额扩到 100 条：单表聚合 15、多表 join 15、时间窗口/同环比 15、留存/漏斗 10、口径歧义 15、拒答 5、权限 10、大表 10、多轮 5。
  - 每条用例记录：`id`、`message`、`user_groups`、`metadata.layer`、`metadata.golden_sql`、`metadata.expected_behavior`（answer/clarify/refuse）。
  - 来源：业务专家（方案负责人）基于合成库人工出题；禁止只用“教科书式”问题。
- **T0-2 golden（改为 golden SQL 现场执行）**
  - 不存 CSV：合成库按天滚动，golden 结果集会立即过期。改为保存人工确认过的 golden SQL，评测时与 agent SQL 在同一份数据上执行后比较。
  - 时间敏感问题一律用“最近 N 个完整自然日”（`toStartOfDay(now()) - INTERVAL 30 DAY` 至 `toStartOfDay(now())`），并记录 `run_at`。
  - 需静态回归时才额外导出 CSV，且必须同时冻结数据快照。
- **T0-3 `ResultSetEvaluator`**
  - 仓库内置的 `ExpectedOutcome` 只支持“调用了哪些工具 / 答案包含哪些词”，**不支持结果集对比**，必须自定义。
  - 从 `AgentResult.tool_calls` 中提取所有 `run_sql` 的 `arguments["sql"]`，逐条重放执行，任一条结果集匹配 golden 即通过。
  - 归一化规则：列名小写去空格、按 golden 对齐列、行序无关（默认）、数值 `rel_tol=1e-6`、NULL 与空串区分。
  - 另需 `ClarificationEvaluator`（该问才问）与 `FirstTryEvaluator`（首答命中率）。
- **T0-4 运行器**
  - 用 `EvaluationRunner`（`core/evaluation/runner.py`）组织，`max_concurrency` 8~16。
  - 输出：HTML 报告 + 失败明细（含失败分类 code）。
- **T0-5 观测**
  - 接 `LoggingObservabilityProvider` + `LoggingAuditLogger`；每次请求记录：模型、prompt 长度、工具调用序列、每步耗时、最终状态。
  - 失败日志必须能还原“模型看到了什么上下文”。

#### 代码骨架

```python
# analytics_agent/evaluation/evaluators.py
import pandas as pd

from vanna.core.evaluation import (
    Evaluator, TestCase, AgentResult, EvaluationResult,
)
from analytics_agent.sql.guardrails import execute_readonly
from analytics_agent.sql.result_norm import normalize_frame, frames_equal


class ResultSetEvaluator(Evaluator):
    @property
    def name(self) -> str:
        return "result_set"

    async def evaluate(self, test_case: TestCase, agent_result: AgentResult) -> EvaluationResult:
        golden_path = test_case.metadata.get("golden_result")
        if not golden_path:
            return EvaluationResult(
                test_case_id=test_case.id, evaluator_name=self.name,
                passed=False, score=0.0, reasoning="missing golden_result",
            )

        golden = normalize_frame(pd.read_csv(golden_path))
        sqls = [
            call["arguments"].get("sql", "")
            for call in agent_result.tool_calls
            if call.get("tool_name") == "run_sql"
        ]
        for sql in reversed(sqls):
            try:
                df = await execute_readonly(sql)
            except Exception:
                continue
            if frames_equal(normalize_frame(df), golden):
                return EvaluationResult(
                    test_case_id=test_case.id, evaluator_name=self.name,
                    passed=True, score=1.0, reasoning=f"matched: {sql[:120]}",
                )

        return EvaluationResult(
            test_case_id=test_case.id, evaluator_name=self.name,
            passed=False, score=0.0,
            reasoning=f"no matching result set; executed {len(sqls)} queries",
        )
```

#### 验收

- `python -m analytics_agent.evaluation.cli run` 一条命令跑完全部用例，产出 HTML/JSON。
- 基线 EX 有明确数字，失败按 taxonomy 分类统计。
- 同一份代码连跑两次，EX 波动 ≤ 2pt（否则说明评测不稳定，先修评测）。

---

### Stage 1：静态上下文工程（P1，3~5 天）

> **详细执行步骤见 [`docs/STAGE1-CONTEXT.md`](./STAGE1-CONTEXT.md)**（含 schema 抽取 SQL、JSON 结构、
> 注释规范、L0/L1/L2 提示词全文、接线代码、D1~D5 每日任务与 G1 验收）。本章只保留总体要求。

**目标**：消除 `SCHEMA_MISSING` 与 `DIALECT_ERROR` 两类错误，并让 E/F/G 的澄清、拒答、无权限规则生效。

#### 任务

- **T1-1 schema 目录自动生成**
  - 从 `system.columns` + `system.tables` 抽出：库、表、字段、类型、注释、默认值、引擎、行数。
  - 落成 JSON（`analytics_agent/context/schema_catalog.json`），启动时加载；提供刷新脚本，**只在 DDL / 枚举取值变化时跑**（行数不渲染进 prompt，避免每日漂移）。
  - 字段注释优先读数据库 `COMMENT`（12 表 / 77 字段已全量补齐），`annotations.yaml` 仅作可选补充；人工抽检 20%。
- **T1-2 L0 稳定指令**
  - 方言规则、硬约束、时间窗口、行为规则、输出格式、工作流，写进 `prompts/templates.py`。
  - 通过 `DefaultSystemPromptBuilder` 子类追加，**不要用 `base_prompt`**（会覆盖默认的记忆工作流段落）。
- **T1-3 L1 常驻 schema**
  - 12 张表的紧凑 schema：表用途、字段、类型、注释、枚举取值。
  - 表数超过 20 张时，L1 只放“领域分组 + 核心表”，具体表交给 Stage 2 的检索工具。
- **T1-4 L2 指标与术语**
  - 每条：术语、定义、计算公式、易混淆项、权威来源。
  - 与 T3 的指标工具共用同一份数据源，避免两处维护。
- **T1-5 接线与 prompt 版本**
  - `Settings` 增加 `schema_catalog_path` / `glossary_path`；`build_agent` 注入 `AnalyticsPromptBuilder`，缺文件启动即报错。
  - `PROMPT_VERSION` 写入 `report.json` 的 `agent_info`，保证结果可追溯。
  - `temperature=0.0`、`max_tool_iterations=8` 已在 Stage 0 固化，本阶段不改。
- **T1-6 验证与 G1**
  - 同配置跑两次 30 条；记录 EX / 首答 / behavior / 平均轮次 / P95，两次差异 ≤ 5pt。
  - 失败按 taxonomy 归因，每轮只修 Top1 类；勾选 G1 并填写 `STAGE1-CONTEXT.md` 第 9 节。

#### 代码骨架

```python
# analytics_agent/prompts/builder.py
from vanna.core.system_prompt import DefaultSystemPromptBuilder

from .templates import L0_RULES


class AnalyticsPromptBuilder(DefaultSystemPromptBuilder):
    def __init__(self, schema_text: str, glossary_text: str):
        super().__init__()  # 保留默认角色与工具清单
        self._schema_text = schema_text
        self._glossary_text = glossary_text

    async def build_system_prompt(self, user, tools):
        base = await super().build_system_prompt(user, tools) or ""
        return "\n\n".join([
            base,
            L0_RULES,
            self._schema_text,      # L1：12 张表的紧凑 schema
            self._glossary_text,    # L2：指标与术语
        ])
```

```python
# prompts/templates.py（节选）
L0_RULES = """
## 数据库与方言
- 数据库：ClickHouse，库名 shop，引擎 MergeTree
- 时间：用 toStartOfDay()/toStartOfMonth()/today()，禁止 DATE_TRUNC/EXTRACT
- 聚合：用 count()，禁止 count(1)；禁止 SELECT *，明细必须带 LIMIT
- 金额：源字段为“分”，输出时 /100.0

## 硬约束
1. 只生成只读 SQL（SELECT / WITH / EXPLAIN）
2. 经营指标排除测试与软删：is_test = 0；users.deleted_at IS NULL
3. 有效订单：status IN ('paid','shipped','completed')
4. schema 已在下方给出，不要查询 system.* 元数据表

## 工作流
1) 识别指标与时间范围；歧义时先澄清（最多 2 个问题）
2) 定位候选表与字段（schema 已在上下文中）
3) 生成一条 SQL 并执行
4) 执行失败则读错误信息修正，禁止原样重试
5) 结论先行 + 展示数据 + 说明口径假设
"""
```

#### 验收

- `SCHEMA_MISSING` + `DIALECT_ERROR` 合计占比从基线降到 < 10%。
- EX 提升 ≥ 15pt。
- prompt 常驻部分 ≤ 2000 token（用 tokenizer 实测）。

---

### Stage 2：工具面重构（P2，1 周）

**目标**：让模型能“先探查、再写 SQL”，而不是靠猜；同时把护栏做对。

#### 工具清单

| 工具 | 参数 | 返回给 LLM 的内容 | 说明 |
|---|---|---|---|
| `search_columns` | `keyword: str`, `limit: int = 20` | `库.表.字段 类型 -- 注释` | 按业务词模糊搜字段，替代全量 schema |
| `describe_table` | `table: str` | 字段列表 + 枚举 Top20 + 行数 + 引擎 | 单表深挖 |
| `sample_rows` | `table: str`, `n: int = 5` | 前 n 行（脱敏） | 确认日期格式/枚举大小写/脏数据 |
| `get_metric_definition` | `name: str` | 定义 + SQL 片段 + 易错点 | 口径唯一权威来源 |
| `explain_query` | `sql: str` | 预估扫描行数/分区裁剪情况 | 大表前置检查 |
| `run_sql` | 现有 | 结果表 + 截断标注 | 加 AST 只读校验、LIMIT 注入、超时 |
| `visualize_data` | 现有 | 图表 | 保持 |

#### 任务

- **T2-1** 实现 `search_columns` / `describe_table`（数据来自 T1-1 的 catalog，无需每次查库）。
- **T2-2** 实现 `sample_rows` / `explain_query`（直接查 ClickHouse，`LIMIT` 强制 + 超时）。
- **T2-3** 实现 `get_metric_definition`（数据源与 T1-4 共用）。
- **T2-4** 工具 description 重写：写清**触发条件 + 禁忌 + 与其他工具的分工**。这是工具路由的主要依据。
- **T2-5** 参数收敛：`granularity`、`time_range`、`order` 等用 `Literal`/枚举，减少自由发挥。
- **T2-6** `run_sql` 护栏改造：
  - 只读校验改用 AST（`sqlglot`）或依赖数据库只读账号，避免关键字黑名单误杀（当前实现会把字符串字面量里的 `'delete'` 判为写操作）。
  - 无 `LIMIT` 时自动追加（可通过 prompt 提示“已自动加 LIMIT”，避免模型误解结果）。
  - 超时通过 ClickHouse `settings={"max_execution_time": 30}` 实现（`ClickHouseRunner.BLOCKING=True`，外层 `wait_for` 打不断线程）。
  - 结果超过 `max_result_chars` 时，返回“已截断 + 总行数 + 总列数”，避免模型误判为全量。
- **T2-7** 错误结构化：把 `sql_validation` 的 `error_type` 和修正建议一起回喂，提高自我修复率。

#### 代码骨架

```python
# analytics_agent/tools/search_columns.py
from typing import Type

from pydantic import BaseModel, Field

from vanna import Tool, ToolContext, ToolResult


class SearchColumnsArgs(BaseModel):
    keyword: str = Field(description="业务关键词，如 'gmv'、'渠道'、'支付时间'")
    limit: int = Field(default=20, ge=1, le=50)


class SearchColumnsTool(Tool[SearchColumnsArgs]):
    def __init__(self, catalog):
        self.catalog = catalog

    @property
    def name(self) -> str:
        return "search_columns"

    @property
    def description(self) -> str:
        return (
            "按业务关键词查找字段。当你不确定字段名、不确定某指标由哪些字段计算、"
            "或需要列出某张表的候选字段时调用。不要用它执行查询。"
        )

    def get_args_schema(self) -> Type[SearchColumnsArgs]:
        return SearchColumnsArgs

    async def execute(self, context: ToolContext, args: SearchColumnsArgs) -> ToolResult:
        hits = self.catalog.search(args.keyword, limit=args.limit)
        if not hits:
            return ToolResult(
                success=True,
                result_for_llm=f"未找到与 '{args.keyword}' 相关的字段，请换用其他业务词。",
            )
        text = "\n".join(
            f"{h.table}.{h.column}  {h.type}  -- {h.comment}" for h in hits
        )
        return ToolResult(success=True, result_for_llm=text)
```

#### 验收

- 评测集上平均工具轮次 ≤ 3.5，且 `TOOL_MISUSE` 占比 < 5%。
- 人工抽检 20 条轨迹：不存在“先跑 SQL 报错再找字段”的低效路径。
- 复杂查询（>3 表 join）成功率提升 ≥ 10pt。

---

### Stage 3：检索层（P3，1~2 周）

**目标**：解决“长尾问题”和“口径复用”，把人工经验变成可检索资产。

#### 任务

- **T3-1 换记忆后端**
  - 要求：持久化、支持中文 embedding、支持 metadata 过滤、可 bulk 导入。
  - 候选：Chroma（`ChromaAgentMemory` 需自定义 embedding function）、Qdrant、Milvus。
  - 注意：`ChromaAgentMemory` 存储时不写 `user_id`，查询也不按用户过滤，属于**全局共享**；如需按团队隔离必须自行包装（或 `metadata` 过滤）。
- **T3-2 两通道数据**
  - 文本记忆：口径、坑、字段含义、方言注释。**一条一知识点**，单条 ≤ 200 字。
  - 工具记忆：golden `question → {"sql": ...}`，人工审核后入库；同一 SQL 配 3~5 种真实问法。
- **T3-3 混合检索**
  - BM25（字段名/业务词精确命中）+ 向量召回，取并集后 rerank。
  - 参数（top-k、阈值、rerank 权重）用评测集网格搜索，不拍脑袋。
- **T3-4 候选表预筛**
  - 用问题检索出 5~15 张候选表，注入到 system prompt 或会话首条上下文。
  - 表数 > 50 时这一步是决定性的；表数 < 20 时可关闭。
- **T3-5 注入位置**
  - 会话级：`enhance_system_prompt`（每条消息一次）。
  - 每步级：`enhance_user_messages`（每次重建请求都跑，不污染历史）。
  - **避免**用工具返回大量 skill/文档正文：工具结果会写回会话永久占 token。
- **T3-6 种子导入**
  - 幂等：以 `(question, tool_name, sql_hash)` 去重。
  - 版本化：种子数据放 git，禁止手工改库。

#### 验收

- 检索 recall@5 ≥ 0.9（离线评测）。
- 端到端 EX ≥ 80%。
- 单问题 token 相比 Stage 2 不增加（说明检索替代了长 prompt）。

#### 风险

- 错误记忆会被反复复用 → 必须有审核与回滚机制（见 Stage 5）。
- embedding 模型与运行时不一致会导致“检索到但语义无关” → 固定版本并写进配置。

---

### Stage 4：编排与交互（P4，1 周）

**目标**：复杂问题的流程一致性、该问就问、结果好读。

#### 任务

- **T4-1 工作流固化**
  - 简单问题：直接查（不强制走记忆检索）。
  - 复杂问题：澄清 → 探查 → 生成 → 执行 → 自检 → 总结。
  - 通过 prompt + 少量 skill 实现，不要为每种问题写一个工具。
- **T4-2 Skill 机制**
  - `list` 常驻（name + 一句话），`load_skill` 按需加载正文。
  - 首批 skill：留存、同环比、漏斗、归因、大表优化、口径争议处理。
  - 控制总量 ≤ 10 个，超过则分两级（领域 → 子技能）。
- **T4-3 澄清策略**
  - 触发条件：指标歧义、时间范围缺失、维度不明确、权限不足。
  - 一次最多问 2 个问题，且给出默认选项。
  - 用 `workflow_handler` 处理确定性命令（`/help`、`/status`），不要消耗 LLM。
- **T4-4 多轮与指代**
  - `SlidingWindowFilter` 或 `SummaryBufferFilter` 控历史长度。
  - 把上一轮 SQL 作为结构化状态传入（而不是依赖聊天历史），提高“上个月呢”这类改写成功率。
- **T4-5 呈现规则**
  - 结论先行（1~2 句），再给数据，最后给口径假设。
  - 图表选择：趋势折线、类别柱状、占比饼图（≤ 6 类）、分布直方图。
  - 单位与精度统一，金额默认元、比率保留 2 位小数。
- **T4-6 自适应迭代**
  - 简单问题 `max_tool_iterations` 3~4，复杂问题 8~10（可按用户/场景配置）。

#### 验收

- 「应澄清」用例澄清率 ≥ 85%，「不应澄清」用例多余提问 ≤ 5%。
- 多轮成功率 ≥ 75%。
- 人工评分（1~5）平均 ≥ 4.0，主要扣分项不再是“答非所问”。

---

### Stage 5：学习闭环与治理（P5，持续）

**目标**：让系统随时间变好，而不是随时间退化。

#### 任务

- **T5-1 高质量轨迹沉淀**
  - `lifecycle_hooks.after_tool` 中校验：执行成功 + 行数 > 0 + 无警告 + 用户未负反馈。
  - 通过审核（人工或 LLM 复核）后写入工具记忆。
  - 默认仅 admin 具备写权限（现有 `SaveQuestionToolArgsTool` 已是 admin-only，保持）。
- **T5-2 失败驱动迭代**
  - 每周导出失败用例，按 taxonomy 聚类，修 top 2 类。
  - 修复样本立即加入评测集，形成回归防护。
- **T5-3 记忆治理**
  - 去重、过期（> 90 天未命中且口径变更）、按租户隔离、支持回滚。
  - 定期抽样人工复核（每周 20 条）。
- **T5-4 权限与合规**
  - 行级安全走 `ToolRegistry.transform_args`（返回 `ToolRejection`），不要靠 prompt。
  - 审计覆盖：SQL 执行、工具拒绝、记忆写入。
  - 移除 cookie 直接映射 admin 的实现（当前 `examples/test.py` 存在该问题）。
- **T5-5 灰度发布**
  - prompt/检索/工具变更 → 离线评测 → 5% 流量灰度 → 全量。
  - 用 `compare_agents` 对比新旧变体，回归 > 2pt 则回滚。

#### 验收

- 连续 4 周 EX 不下降，且至少提升 1pt/2 周。
- 记忆库中错误条目比例 < 2%（抽样）。
- 变更回滚时间 < 10 分钟（配置化开关）。

---

## 6. 阶段门禁（Gate）

进入下一阶段前必须同时满足：

| Gate | 条件 |
|---|---|
| G0 → G1 | 评测可一键运行；基线数字稳定（两次差异 ≤ 2pt） |
| G1 → G2 | EX ≥ 25%（+10pt）；schema 类错误 < 5%；behavior ≥ 70% |
| G2 → G3 | 平均工具轮次 ≤ 3.5；复杂查询 +10pt |
| G3 → G4 | EX ≥ 80%；recall@5 ≥ 0.9；token 不劣化 |
| G4 → G5 | 澄清准确率 ≥ 85%；多轮 ≥ 75%；人工评分 ≥ 4.0 |
| G5 常态 | 每周 EX 不下降；错误记忆 < 2% |

任何一项不达标，先修当前阶段，不允许“带病推进”。

---

## 7. 排期与里程碑

| 周 | 内容 | 里程碑 |
|---|---|---|
| W1 | Stage 0（合成库 + 30 题 + 基线，见 `STAGE0-EVAL.md`） | G0 通过，有稳定基线 |
| W2 | Stage 1（schema + L0/L1/L2 + 行为规则） | G1 通过：EX ≥ 25%、schema < 5%、behavior ≥ 70% |
| W3 | Stage 2（工具面 + 护栏） | 轮次 ≤ 3.5，复杂查询 +10pt |
| W4~W5 | Stage 3（检索层 + 候选表预筛） | EX ≥ 80%，recall@5 ≥ 0.9 |
| W6 | Stage 4（编排/skill/澄清/呈现） | 澄清 ≥ 85%，多轮 ≥ 75% |
| W7+ | Stage 5 + 灰度 | 进入持续迭代 |

人力假设：1 名后端/数据工程师全职 + 0.2 名业务专家（口径与 golden 审核）。业务专家的参与是硬依赖——本项目由方案负责人本人承担该角色，需在 W1 D2 前完成口径确认，否则 Stage 1 与 Stage 3 会阻塞。

---

## 8. 风险登记

| 风险 | 影响 | 概率 | 应对 |
|---|---|---|---|
| golden 结果集本身错误 | 评测失真，优化方向错误 | 中 | 双人复核；golden SQL 纳入版本管理 |
| 口径频繁变更 | 记忆与 prompt 过期 | 高 | 口径表单一来源 + 版本号；变更触发缓存失效 |
| 大表查询打爆数据库 | 生产事故 | 中 | 只读账号 + `max_execution_time` + `EXPLAIN` 前置 + 资源队列 |
| 记忆污染 | 错误被反复复用 | 中 | 写入审核 + 抽样复核 + 回滚 |
| 上下文膨胀 | 成本上升、注意力稀释 | 高 | 预算上限 + 每项内容必须有评测收益 |
| 提示词漂移 | 指标不可比 | 中 | prompt 版本化，评测报告记录版本号 |
| 大 schema 库检索不准 | 表选错 | 中 | 候选表预筛 + BM25 混合 + 人工别名表 |

---

## 9. 进度追踪

> 开发时直接勾选并补充备注。

### Stage 0（执行细节见 [`STAGE0-EVAL.md`](./STAGE0-EVAL.md)）

> 2026-10-23：Stage 0 收尾完成。仅“稳定性复跑”未测，经裁定先进入 Stage 1。

- [x] T0-0 合成业务库（12 表 DDL + 生成脚本 + 只读账号脚本）；0.1 规模实跑通过
- [x] T0-2 golden SQL 机制完成（现场执行，不存 CSV）
- [x] T0-3 `ResultSetEvaluator` + `ClarificationEvaluator` + `FirstTryEvaluator`
- [x] T0-4 一键评测运行器 + HTML/JSON 报告
- [x] T0-5 预检（连接/只读/锚点/golden 自检）
- [x] T0-6 失败 taxonomy：30 条实跑归因完成，`UNKNOWN` = 0%
- [x] T0-0 运行时：`seed --scale 0.1` 实跑通过；2026-10-23 重灌并封顶时间戳
- [x] T0-1 评测集：**30 / 30**（24 条新增，golden 逐条执行验证；5 项口径已裁决）
- [x] T0-1 预检：`check --strict --dataset v1_30.yaml` → **41/41 PASS**（2026-10-23）
- [x] T0-1 业务核对：A~I 全部核对完成（2026-10-23）
- [x] T0-5 运行时：`check --dataset smoke.yaml` **20/20 PASS**
- [x] 基线 EX 记录：**15%（估算）**（实测 0/21，95% 上界 13.6%；复跑波动未测）
- [x] **D1-5 冒烟 run**：4 条；DeepSeek 实跑，`A-02` 走通 `run_sql`（2026-10-23）
- 已验证：`pytest tests/test_eval_*.py tests/test_deepseek_integration.py` **25 passed**；ruff 全通过；CLI/YAML/报告生成均正常；`.env` 可用，DeepSeek 集成已就位（待配 Key 实跑）
- 顺带修复：`vanna/core/evaluation/dataset.py` 补 `encoding="utf-8"`（Windows GBK 下中文用例无法解析）

### Stage 1（执行细节见 [`STAGE1-CONTEXT.md`](./STAGE1-CONTEXT.md)）
- [x] T1-1 schema 目录（`context/schema_catalog.json` + 注释合并）——2026-10-25：12 表 / 77 字段 / 16 枚举，19.5 KB；渲染 ≈1,556 tokens
- [x] T1-2 L0 稳定指令（方言/时间窗口/硬约束/行为规则）——2026-10-25：586 tokens；完整提示词 2,268 tokens
- [x] T1-3 L1 常驻 schema 渲染（12 表，≤2,200 tokens）——2026-10-25：4,114 chars ≈1,556 tokens
- [x] T1-4 L2 指标与术语（`glossary.yaml` + E/F/G 行为规则）——2026-10-25：9 指标 + 5 术语，渲染 ≈173 tokens
- [ ] T1-5 接线与 prompt 版本（`system_prompt_builder` + `prompt_version`）
- [ ] T1-6 验证与 G1（两次运行、失败归因、填第 9 节）
- [ ] G1：EX ≥ 25%、behavior ≥ 70%、平均轮次 ≤ 5、schema < 5%、两次差异 ≤ 5pt
- [ ] EX：`____%`（基线 15% 估算）

### Stage 2
- [ ] T2-1 `search_columns` / `describe_table`
- [ ] T2-2 `sample_rows` / `explain_query`
- [ ] T2-3 `get_metric_definition`
- [ ] T2-4 工具 description 重写
- [ ] T2-5 参数收敛
- [ ] T2-6 `run_sql` 护栏改造
- [ ] T2-7 结构化错误回喂
- [ ] 平均轮次：`____`

### Stage 3
- [ ] T3-1 记忆后端替换
- [ ] T3-2 种子数据（文本 + golden SQL）
- [ ] T3-3 混合检索 + rerank
- [ ] T3-4 候选表预筛
- [ ] T3-5 注入位置确定
- [ ] T3-6 幂等导入脚本
- [ ] recall@5：`____` / EX：`____%`

### Stage 4
- [ ] T4-1 工作流固化
- [ ] T4-2 Skill 机制
- [ ] T4-3 澄清策略
- [ ] T4-4 多轮与指代
- [ ] T4-5 呈现规则
- [ ] T4-6 自适应迭代上限
- [ ] 澄清准确率：`____`

### Stage 5
- [ ] T5-1 轨迹沉淀与审核
- [ ] T5-2 失败驱动迭代机制
- [ ] T5-3 记忆治理
- [ ] T5-4 权限与合规
- [ ] T5-5 灰度与回滚

---

## 10. 附录

### A. 评测用例 YAML 示例

```yaml
dataset:
  name: "shop_v1_30"
  description: "自建 ClickHouse 合成业务库评测集"
  test_cases:
    - id: "A-01"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "最近 30 天各渠道的 GMV 是多少"
      metadata:
        layer: A
        difficulty: easy
        # golden 存 SQL 而非 CSV：评测时与 agent 的 SQL 在同一份数据上执行后比较
        golden_sql: |
          SELECT channel, SUM(amount_cents) / 100.0 AS gmv
          FROM shop.orders
          WHERE created_at >= toStartOfDay(now()) - INTERVAL 30 DAY
            AND created_at <  toStartOfDay(now())
            AND status IN ('paid','shipped','completed')
            AND currency = 'CNY' AND is_test = 0
          GROUP BY channel ORDER BY gmv DESC
        expected_behavior: answer

    - id: "E-05"
      user_id: "analyst"
      user_groups: ["analyst"]
      message: "看一下复购率"
      metadata:
        layer: E
        expected_behavior: clarify   # answer | clarify | refuse
```

> 说明：`golden_sql` 与 `expected_behavior` 放在 `metadata` 中，由自定义 `Evaluator` 消费；内置 `ExpectedOutcome` 只支持工具名与关键词匹配，不支持结果集对比。完整样例见 `STAGE0-EVAL.md` 6.3。

### B. ClickHouse schema 抽取 SQL

```sql
-- 字段级 schema
SELECT
    c.database,
    c.table,
    c.name,
    c.type,
    c.comment,
    c.default_kind,
    c.default_expression,
    t.engine,
    t.comment AS table_comment,
    t.total_rows
FROM system.columns AS c
INNER JOIN system.tables AS t
    ON c.database = t.database AND c.table = t.name
WHERE c.database = 'default'
  AND t.engine NOT LIKE '%View'
ORDER BY c.table, c.position;
```

```sql
-- 低基数字段取值采样（对每个候选枚举字段执行一次）
SELECT event_name, count() AS cnt
FROM default.events
GROUP BY event_name
ORDER BY cnt DESC
LIMIT 20;
```

### C. 提示词版本记录

| 版本 | 日期 | 变更 | 评测结论 |
|---|---|---|---|
| v1 | 2026-10-21 | 初始 L1 规则 | 待测 |

### D. 术语表

| 术语 | 含义 |
|---|---|
| EX | 执行准确率，结果集与 golden 一致的比例 |
| Gate | 阶段门禁，未达标不进入下一阶段 |
| L0~L4 | 五层上下文：稳定指令 / 常驻 schema / 指标术语 / 按需事实 / 运行时状态 |
| taxonomy | 失败分类法，见 2.3 |
| 候选表预筛 | 先用检索把大 schema 缩到 5~15 张表，再让模型写 SQL |
