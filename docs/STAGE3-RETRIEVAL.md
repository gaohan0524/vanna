# Stage 3 执行手册：检索层与知识复用

> 本文是 `docs/AGENT_QUALITY_PLAN.md` 中 **Stage 3 / T3-1 ~ T3-6** 的详细执行版。
>
> - 状态：**进行中（2026-10-28，T3-1 待开始）**
> - 前置：Stage 2 完成；G2 基线冻结为 v8 两次结果
> - Stage 2 基线：EX **81.8% / 86.4%**、behavior **100%**、平均工具轮次 **1.27~1.43**、P95 **15.1s / 14.5s**、两次差异 **4.5pt**
> - 当前代码：`stage2-v10`；v9/v10 的 B-05、B-02、I-30 修正是**基线后未复评变更**
> - 目标：**recall@5 ≥ 0.9**、**EX ≥ 80%**、单问题 token 不高于 Stage 2
> - 最后更新：2026-10-28

---

## 0. 从 Stage 2 继承的事实

### 0.1 基线（v8，30 条）

| 指标 | 值 |
|---|---|
| EX | 81.8%（18/22） / 86.4%（19/22） |
| first_try | 81.8% / 86.4% |
| behavior | 100%（8/8） |
| 平均工具轮次 | 1.43 / 1.27 |
| P95 | 15.1s / 14.5s |
| 两次 EX 差异 | 4.5pt |
| 提示词 | `stage2-v8`，3,004 tokens（当前代码已到 v10） |

报告：

- `reports/20261027-165653/report.html`
- `reports/20261027-170011/report.html`

### 0.2 当前代码状态

| 项 | 状态 |
|---|---|
| `stage2-v10` | B-05 CNY、B-02 净收入、I-30 单值规则已改，**未复评** |
| Eventual EX | 已加入报告，但 v8 报告没有该字段 |
| `TOOL_MISUSE` | 未人工统计 |
| 复杂查询通过率 | 未人工统计 |
| first_try 抖动 | B-02 / D-04 / C-15 曾出现首答失败、最终正确 |

Stage 3 的对比基线仍是 **v8**；当前 v10 不能直接冒充 v8。若需要做严格的检索 A/B，必须额外跑一个“当前代码、不开启检索”的对照组，再与“当前代码、开启检索”比较。这个对照属于 Stage 3 评测，不是重新给 Stage 2 打分。

### 0.3 Stage 2 已冻结、Stage 3 不改的东西

- 30 条评测用例与 golden（除非发现真实错误）
- v8 的 G2 基线数字
- Stage 1/2 的 L1 schema、L2 指标结构
- `run_sql` 的 AST 护栏、LIMIT、30s 超时、结构化错误
- 评测命令与报告格式

### 0.4 当前记忆接线（代码事实）

当前 `analytics_agent/agent_factory.py`：

```python
agent_memory=agent_memory or DemoAgentMemory(max_items=10_000)
enable_memory_tools: bool = False
```

含义：

- 默认仍是 **内存版 DemoAgentMemory**，重启即丢；
- 默认不注册记忆工具；
- `Agent` 如果没有显式传入 `llm_context_enhancer`，会自动创建 `DefaultLlmContextEnhancer(agent_memory)`；
- `DefaultLlmContextEnhancer` 只做一件事：用用户问题检索 **文本记忆 top-5**，拼到 system prompt 末尾。

真实代码路径：

```text
src/vanna/capabilities/agent_memory/base.py     AgentMemory 抽象接口
src/vanna/capabilities/agent_memory/models.py   ToolMemory / TextMemory / 搜索结果
src/vanna/integrations/local/agent_memory/      DemoAgentMemory
src/vanna/integrations/chromadb/agent_memory.py ChromaAgentMemory
src/vanna/core/enhancer/default.py              DefaultLlmContextEnhancer
src/vanna/core/agent/agent.py                  enhancer 调用点
analytics_agent/agent_factory.py               当前接线
```

已知限制：

| 限制 | 影响 |
|---|---|
| `DemoAgentMemory` 用 Jaccard/difflib，中文按空格分词 | 中文问题召回差 |
| `ChromaAgentMemory` 不写 `user_id`，查询也不按用户过滤 | 全局共享，不能直接用于多租户 |
| Chroma 默认 embedding 对中文不友好 | 语义召回质量不稳定 |
| 当前没有检索评测集 | recall@5 无法度量 |
| 当前 L1 常驻 12 张表 | 加检索后 token 容易上涨 |

### 0.5 本阶段要解决的痛点

1. 长尾口径只存在于 prompt 里，越加越长；
2. 相似问题每次重新生成 SQL，首答抖动；
3. 人工修过的 golden 经验无法复用；
4. 表/字段/指标知识没有检索入口；
5. 候选表预筛缺失，schema 只能全量注入。

---

## 1. 目标与 G3 门禁

### 1.1 目标

1. **知识资产化**：把口径、坑、字段别名、SQL 模板变成可检索数据。
2. **相似问题复用**：同义问法能召回已审核的 SQL/口径。
3. **候选表预筛**：先找 5~15 张候选表，再决定注入哪些 schema。
4. **上下文不膨胀**：检索替代长 prompt，单问题 token 不高于 Stage 2。
5. **可运维**：幂等导入、版本化、可回滚、按团队隔离。

### 1.2 G3 验收（全部满足才算通过）

- [ ] **recall@5 ≥ 0.9**（离线检索评测）
- [ ] **端到端 EX ≥ 80%**，不低于 Stage 2 v8 基线
- [ ] **单问题 token 不高于 Stage 2**（同一统计口径）
- [ ] 检索失败可降级：不回退到报错，主流程继续
- [ ] 种子导入幂等：重复执行不产生重复记录
- [ ] 记忆按用户/团队隔离，不串数据
- [ ] 错误记忆可回滚
- [ ] 检索与导入有单测

> 说明：v8 第二次已经 86.4%，所以 Stage 3 的 EX 目标不是“从 70 冲到 80”，而是**证明加了检索后不明显回落，并拿到 recall/token 收益**。  
> 如果新检索导致 EX 下降，宁可先关掉检索开关，也不要用“平均分没降”掩盖单条回归。

### 1.3 非目标

- 不在本阶段做自动写入记忆（Stage 5）
- 不在本阶段做用户反馈采集与管理员审核 CRUD（Stage 5）
- 不在本阶段做完整工作流/skill（Stage 4）
- 不重写 Stage 2 的工具和 SQL 护栏
- 不为每种问题新增专用工具
- 不追求生产级多租户权限，只先做可验证的隔离字段与过滤

---

## 2. 设计

### 2.1 两类记忆，两条通道

| 通道 | 内容 | 示例 |
|---|---|---|
| 文本记忆 | 口径、坑、字段含义、方言注释 | “B-02 品类净收入必须以订单金额为基数” |
| 工具记忆 | 已审核的 `question → SQL` | “近 30 天各品类净收入” → 对应 golden SQL |

文本记忆用于**解释**，工具记忆用于**复用**。两者不能混在一个 collection 里只靠 `type` 字段区分，检索时要分别取。

### 2.2 检索流程

```text
用户问题
  → 文本记忆检索（BM25 + 向量）
  → 工具记忆检索（问题 → SQL）
  → 候选表检索（5~15 张）
  → 去重 / rerank / 预算裁剪
  → 注入 system prompt 或首条 user message
  → LLM 生成 SQL
```

### 2.3 混合检索

```text
BM25（字段名、业务词、表名精确命中）
  + 向量召回（中文语义相似）
  → 并集去重
  → rerank
  → top-k
```

要求：

- BM25 必须支持中文：优先 `jieba` 分词，其次字符 bigram；不能沿用“按空格切词”。
- 向量模型固定版本，写入配置；不能今天 BGE、明天 OpenAI。
- 相似度阈值和 top-k 通过评测集网格搜索，不拍脑袋。
- 检索结果必须带 `source`、`version`、`score`，方便排查。

### 2.4 候选表预筛

- 输入：用户问题 + 表名/表注释。
- 输出：5~15 张候选表（表名 + 一句话用途）。
- 表数 < 20 时可关闭；> 50 时必须开启。
- 候选表召回失败时，回退到全量 L1；不能直接空 schema。
- 候选表只决定“注入哪些表的 schema”，不代替 `search_columns` 的字段级检索。

### 2.5 注入位置

| 位置 | 用途 | 注意 |
|---|---|---|
| `enhance_system_prompt` | 会话级：文本记忆 + 候选表 | 每轮一次，适合稳定知识 |
| `enhance_user_messages` | 每步级：当前问题相关的 SQL 示例 | 每次重建请求都跑，不污染历史 |
| 工具返回 | **不要**放长文档 | 工具结果会写回会话，永久占 token |

Stage 3 优先实现 `enhance_system_prompt`；只有在 SQL 示例需要随工具轮次变化时，才用 `enhance_user_messages`。

### 2.6 token 预算

| 项 | 预算 |
|---|---|
| 当前完整提示词 | 3,009 tokens（`stage2-v10`） |
| 检索注入 | ≤ 400 tokens |
| 候选表 schema | 目标 5~8 张，替代全量 12 张 |
| 总提示词 | **≤ 3,009 tokens** |

如果 12 张表时候选表预筛关闭、检索又增加 400 tokens，就不可能“不增加”。此时必须二选一：

1. 开启候选表预筛，压掉 L1；
2. 接受 token 上升，但必须记录例外并经过确认。

不能悄悄把 G3 的 token 门槛改成“略增”。

---

## 3. 目录与文件

```text
analytics_agent/
├── memory/
│   ├── __init__.py
│   ├── backends/
│   │   ├── __init__.py
│   │   └── chroma_memory.py     # Chroma + 中文 embedding + 租户过滤包装
│   ├── seeding.py               # 幂等导入 / dry-run / 回滚
│   ├── retrieval.py             # BM25 + 向量 + rerank
│   ├── candidate_tables.py      # 候选表预筛
│   ├── enhancer.py              # 自定义 LlmContextEnhancer
│   └── seeds/
│       ├── knowledge.yaml       # 文本记忆
│       ├── tool_memories.yaml   # question -> SQL
│       └── manifest.yaml        # 版本 / 计数 / hash
├── evaluation/
│   └── retrieval/
│       ├── cases.yaml           # 检索评测 query + 相关资产
│       └── evaluate.py          # recall@5 离线评测
└── agent_factory.py             # 注入 memory + enhancer

tests/
├── test_memory_import.py
├── test_retrieval.py
├── test_candidate_tables.py
└── test_memory_isolation.py
```

当前已有、需要复用的文件：

```text
src/vanna/capabilities/agent_memory/base.py
src/vanna/capabilities/agent_memory/models.py
src/vanna/integrations/chromadb/agent_memory.py
src/vanna/core/enhancer/default.py
src/vanna/core/enhancer/base.py
analytics_agent/context/schema_catalog.json
analytics_agent/context/glossary.yaml
analytics_agent/evaluation/datasets/v1_30.yaml
```

---

## 4. T3-1：记忆后端替换

### 4.1 目标

把 `DemoAgentMemory` 换成持久化、支持中文语义、可过滤、可批量导入的后端。

### 4.2 选型

#### 4.2.1 当前代码与环境事实

| 维度 | Chroma | Qdrant |
|---|---|---|
| 客户端安装 | ✅ `chromadb` 已安装 | ❌ `qdrant_client` 未安装 |
| 现有实现 | ✅ `ChromaAgentMemory`：工具/文本/删除/自定义 EF | ⚠️ `QdrantAgentMemory`：接口齐全，但 embedding 是 **MD5 占位向量** |
| 部署方式 | `PersistentClient`（本地目录）；不是 HTTP server 客户端 | 支持 `url` / `path`；适合独立服务 |
| 中文语义 | 可挂自定义 embedding；默认 EF 中文弱 | 需自己接 embedding；当前占位实现不可用 |
| metadata 过滤 | 支持 `where` | 支持 `Filter`/payload，表达力更强 |
| 用户隔离 | 当前不写 `user_id` | 当前不写 `user_id` |
| 幂等 ID | `upsert(ids=...)` 可做确定性 ID | 当前是随机 UUID；需改确定性 ID |
| 规模 | 小/中 corpus 足够 | 更适合大数据量、多租户、强过滤 |
| 运维 | 单机目录，简单 | 独立服务，需管理服务健康/备份 |

> 本机 `127.0.0.1:8000` / `127.0.0.1:6333` 未监听；远端 `192.168.0.193:8000` 的 Chroma 容器可连通。  
> `.env` 目前没有 `CHROMA_*` / `QDRANT_*` 配置；上面的代码事实不依赖服务是否在线。

#### 4.2.2 暂定决策

**Stage 3 主实现选 Chroma**，原因：

1. 现有实现最完整，文本记忆、工具记忆、删除、自定义 embedding 都已具备；
2. `chromadb` 已安装，能最快跑通“种子 → 检索 → 评测”；
3. 当前 corpus 只有几十到几百条，Chroma 的规模和过滤能力足够；
4. Qdrant 当前实现还需要先替换占位 embedding、补确定性 ID、补用户过滤，属于额外改造成本。

**Qdrant 作为生产/规模化备选**，满足任一条件再切换：

- 需要多租户强过滤或高并发；
- corpus 超过几万条；
- Chroma 的 recall@5 或 P95 明显不如 Qdrant；
- Chroma 以 HTTP server 部署，而现有 `PersistentClient` 不兼容，改造成本高于 Qdrant。

#### 4.2.3 公平对比门槛

不能只按“哪个库更强”拍脑袋。用同一批种子、同一个中文 embedding、同一批 query 做最小 A/B：

| 项 | 要求 |
|---|---|
| 种子 | 20 条文本 + 15 条工具记忆 |
| query | 30 条检索评测 query |
| embedding | 同一个中文模型（如 `BAAI/bge-small-zh-v1.5`） |
| 指标 | recall@5、P95 检索延迟、过滤正确性、二次导入幂等 |
| 选择规则 | Chroma 在 recall 上不落后超过 3pt 且延迟可接受 → 保持 Chroma；Qdrant 领先 ≥5pt 或强隔离/规模刚需 → 切 Qdrant |

先做 Chroma，不代表永远不换；后端通过 `AgentMemory` 接口隔离，切换成本应控制在适配器层。

#### 4.2.4 本地 `PersistentClient` vs 远端 HTTP Chroma

同一个 Chroma 引擎，**检索质量不会有本质差距**，前提是：

- 同一个中文 embedding 模型；
- 同一个 collection 参数（distance=cosine / hnsw 配置）；
- 同一批数据和相同版本。

差异主要在工程侧，不在“向量算法”：

| 维度 | 本地目录 `PersistentClient` | 远端 `HttpClient`（192.168.0.193:8000） |
|---|---|---|
| 检索质量 | 同模型/同参数下基本一致 | 基本一致；版本或索引状态不同可能有少量 top-k 差异 |
| 延迟 | 进程内调用，最低 | 多一次 LAN HTTP + JSON 序列化，通常仍很小 |
| 并发/多进程 | 单进程友好；多进程写同一目录可能锁冲突 | 服务端集中处理，多客户端/多 worker 更合适 |
| 数据共享 | 本机独占 | 多台机器/多个服务共享 |
| 可复现 | 目录可打包/快照，最适合离线评测 | 共享可变状态，复现要固定 collection/版本 |
| 运维 | 无服务依赖 | 需要容器健康、备份、版本升级 |
| 当前代码 | ✅ `ChromaAgentMemory` 直接支持 | ❌ 当前类只实现 `PersistentClient`，要另写 `HttpClient` 适配器 |

**Stage 3 决策：本地目录优先。**

原因：

1. Stage 3 是单进程离线评测，不需要多客户端共享；
2. 本地目录可快照、可重建，A/B 更可复现；
3. 不需要改现有 `ChromaAgentMemory`；
4. 30~100 条种子下，远端 HTTP 的并发优势用不上，反而多一层网络和版本耦合。

保留远端容器作为未来选项：

```text
MEMORY_BACKEND=chroma_local   # Stage 3 默认
MEMORY_BACKEND=chroma_http    # 未来多进程/共享时
```

切换时必须使用不同的 collection，禁止本地与远端共用同一个逻辑库名，避免数据分叉。

### 4.3 中文 embedding

Chroma 默认 `DefaultEmbeddingFunction` 是英文 MiniLM，不适合中文业务问题。要求：

- 固定一个中文/多语言 embedding 模型，例如 `BAAI/bge-small-zh-v1.5`；
- 模型名称/版本写进配置和种子 manifest；
- 离线环境预下载模型；
- collection 创建后不能换 embedding，否则必须重建 collection；
- 不能把“同一个 collection 换模型”当成热更新。

### 4.4 租户隔离

`ChromaAgentMemory` 当前不写 `user_id`。不能直接拿它做多用户共享库。

实现方式：

- 包装层在 `save_*` 时把 `user_id` / `tenant_id` 写进 metadata；
- `search_*` 时强制加 `where={"user_id": ...}` 或团队过滤；
- 种子知识可以标记为 `shared`，团队私有记忆标记为 `team`；
- 单测必须证明 A 用户检索不到 B 用户私有记忆。

### 4.5 接口实现

#### 4.5.1 新实现放 `analytics_agent/memory`，不改框架 integration

**结论：新增应用侧 `analytics_agent/memory/backends/chroma_local.py`，不直接改 `src/vanna/integrations/chromadb/agent_memory.py`。**

原因：

- 框架的 `ChromaAgentMemory` 是通用实现，不写 `user_id`、不支持租户过滤、ID 是随机 UUID；
- 我们的需求是应用特有的：中文 embedding、`shared/team/user` 过滤、确定性 ID、种子 manifest；
- 直接改框架文件会影响其他示例/测试，并造成后续上游同步冲突；
- 应用侧实现通过 `AgentMemory` 接口注入，不影响框架代码。

复用方式：

| 复用 | 说明 |
|---|---|
| `AgentMemory` 抽象接口 | 必须完整实现 |
| `ToolMemory` / `TextMemory` 模型 | 直接复用框架模型 |
| `chromadb.PersistentClient` | 直接使用 Chroma 客户端 |
| `ChromaAgentMemory` | 仅作为参考，不继承、不修改 |

目录：

```text
analytics_agent/memory/backends/chroma_local.py
```

工厂注入：

```python
# analytics_agent/agent_factory.py
agent_memory = build_memory(settings)  # chroma_local
```

配置：

```text
MEMORY_BACKEND=chroma_local
MEMORY_PATH=analytics_agent/memory/data/chroma
MEMORY_COLLECTION=analytics_memory_v1
MEMORY_EMBEDDING=BAAI/bge-small-zh-v1.5
```

如果未来确实需要把 `user_id`/metadata 过滤能力贡献给框架，再单独提 PR 修改 `ChromaAgentMemory`，不要在本阶段混在一起。

#### 4.5.2 建议骨架

必须实现 `AgentMemory` 的全部抽象方法；不能只实现 `save_text_memory` / `search_text_memories`。

```python
class ChromaLocalMemory(AgentMemory):
    def __init__(self, persist_directory: str, embedding_function, collection: str):
        ...

    async def save_tool_usage(self, question, tool_name, args, context, ...):
        # metadata: user_id, tenant_id, sql_hash, tables, metrics, source, version
        ...

    async def search_text_memories(self, query, context, *, limit=10, ...):
        # where: shared OR current user/team
        ...
```

### 4.6 降级

- 记忆后端不可用 → 记 warning，返回空检索结果，不阻断主流程；
- 检索异常 → 回退到原有 prompt；
- embedding 不可用 → 不能静默切换模型；应显式失败并提示重建 collection。

### 4.7 DoD

- [ ] `ChromaLocalMemory` 持久化；重启后可检索
- [ ] 中文 query 能召回中文种子
- [ ] `user_id` / `tenant_id` 过滤生效
- [ ] 后端异常不阻断主流程
- [ ] 有最小 smoke test

---

## 5. T3-2：两通道种子数据

### 5.1 文本记忆

一条一知识点，单条 ≤ 200 字，必须带来源。

```yaml
- id: "net_revenue.category_base"
  text: "品类净收入以 orders.amount_cents（已扣优惠）为基数，item_amount_cents 只用于品类分摊比例。"
  tags: ["net_revenue", "B-02", "退款"]
  source: "metrics.md#品类净收入"
  version: 1
```

优先内容：

- 9 个指标定义与易错点
- B-02 / B-05 / D-04 / D-17 / D-18 / C-15 / C-16 的修正口径
- 表/字段别名
- ClickHouse 方言坑
- 周起点、自然日、完整月等时间规则

不要放：

- 大段 golden SQL 正文（放工具记忆）
- 可变的行数/金额
- 未审核的个人猜测

### 5.2 工具记忆

```yaml
- id: "b02_category_net_revenue"
  question: "近 30 天各品类的净收入是多少？"
  paraphrases:
    - "按品类看最近一个月的净收入"
    - "各品类扣退款后的收入"
  tool_name: "run_sql"
  sql: |
    ...
  tables: ["orders", "order_items", "products", "refunds"]
  metrics: ["net_revenue"]
  source: "v1_30.yaml#B-02"
  version: 1
  reviewed: true
```

要求：

- 同一 SQL 配 3~5 种真实问法；
- 必须人工审核 `reviewed: true` 才能入库；
- SQL 去掉尾分号、统一大小写风格后再算 hash；
- 不在种子里存“模型第一次生成的错误 SQL”。

### 5.3 目标规模

| 类型 | 最小 | 目标 |
|---|---:|---:|
| 文本记忆 | 20 条 | 40 条 |
| 工具记忆 | 15 条 | 30 条 |
| 检索评测 query | 30 条 | 50 条 |

先覆盖 22 条 answer 用例，再补同义问法。

### 5.4 DoD

- [ ] `knowledge.yaml`、`tool_memories.yaml` 通过 schema 校验
- [ ] 工具记忆有 3~5 个问法
- [ ] 所有种子有 source/version/reviewed
- [ ] 不存在未审核的模型生成 SQL

### 5.5 与运行时自动写入的边界

Stage 3 **只导入人工审核过的种子**，不在本阶段自动写入。

当前代码事实：

- `build_agent(..., enable_memory_tools=False)` 默认关闭记忆工具；
- `SaveQuestionToolArgsTool` 仅 admin 可调用，且需要模型显式调用；
- `SaveTextMemoryTool` 没有在 `build_tool_registry` 中注册；
- 没有 `after_message` / `after_tool` 的自动记忆钩子；
- `AgentMemory` 有 `delete_*` / `clear_*` 接口，但没有暴露成管理工具/CLI；
- 没有用户反馈采集接口。

自动沉淀、反馈审核、更新/删除属于 **Stage 5（T5-1/T5-3）**：

```text
成功执行
  → 写入 pending_review（不是直接 verified）
  → 用户反馈（正/负）
  → 管理员审核 / 修改 / 拒绝
  → approve 后才进入可检索库
  → 支持版本、回滚、删除
```

如果要把这条链路提前到 Stage 3，应新增 **T3-7 记忆生命周期底座**，并明确：

- 自动写入默认关闭；
- 所有自动记录先进入 `pending_review`；
- 未审核内容不参与检索；
- 管理员批准后才能更新 `verified` 记忆；
- 删除/更新必须留版本记录。

否则 Stage 3 的“种子库”和 Stage 5 的“运行时记忆”会混在一起，无法判断错误来自哪一类。

---

## 6. T3-3：混合检索与 rerank

### 6.1 两路召回

**BM25**：

- 字段名、表名、业务词精确命中；
- 中文分词优先 `jieba`，没有依赖时用字符 bigram；
- 不能按空格切中文。

**向量**：

- 用固定中文 embedding；
- 对 query 和文档做同模型编码；
- 结果带 cosine/L2 score。

### 6.2 合并与 rerank

```text
bm25_top_k + vector_top_k
  → 按 id 去重
  → 归一化分数
  → rerank（轻量规则或 cross-encoder）
  → 按类型分别截断
```

建议初始参数：

| 参数 | 初始值 |
|---|---|
| BM25 top-k | 20 |
| 向量 top-k | 20 |
| 最终文本记忆 | 5 |
| 最终 SQL 记忆 | 3 |
| 最终候选表 | 8 |
| 相似度阈值 | 0.3~0.5（按模型调） |

这些只是起点，必须用评测集网格搜索。

### 6.3 去重

- 文本记忆按 `content_hash` 去重；
- 工具记忆按 `(normalized_sql_hash, tool_name)` 去重；
- 同一个 SQL 的多个问法只保留得分最高的一条，避免重复注入。

### 6.4 DoD

- [ ] BM25 中文可用
- [ ] 向量召回可复现
- [ ] 两路结果可去重/合并
- [ ] 参数可配置，不写死
- [ ] 单测覆盖空结果、重复结果、异常

---

## 7. T3-4：候选表预筛

### 7.1 目标

从问题召回 5~15 张候选表，减少全量 schema 注入。

### 7.2 输入

- 用户问题
- 表名
- 表注释/用途
- 可选：字段名、指标关联表

### 7.3 规则

- 表数 < 20：可以关闭，避免小库过度筛选；
- 表数 > 50：必须开启；
- 召回为空或置信度低：回退全量 L1；
- 指标定义中明确提到的表，强制加入候选集；
- 候选表只注入“表名 + 用途 + 关键字段”，不注入全量字段注释。

### 7.4 输出格式

```text
## Candidate Tables
- shop.orders — 订单主表，金额单位为分
- shop.order_items — 订单明细
- shop.refunds — 订单级退款
```

### 7.5 DoD

- [ ] 30 条问题都能召回非空候选表
- [ ] 关键表不漏（对照检索评测集）
- [ ] 召回失败可回退全量
- [ ] 候选表注入有 token 上限

---

## 8. T3-5：注入位置

### 8.1 选择

| 位置 | 使用场景 |
|---|---|
| `enhance_system_prompt` | 文本知识、候选表、稳定 SQL 模板 |
| `enhance_user_messages` | 需要随当前工具轮次变化的提示 |
| 工具返回 | 只放短结果，不放长文 |

Stage 3 默认：

```text
system prompt:
  - 文本记忆 top-5
  - 候选表 5~8 张
  - SQL 模板 top-3（如果短）

user message:
  - 默认不注入
```

### 8.2 格式

```text
## Retrieved Knowledge
- [net_revenue] 品类净收入以订单金额为基数...
- [B-02] item_amount 只用于分摊比例...

## Similar SQL
- 问题：近 30 天各品类净收入
  SQL: ...

## Candidate Tables
- shop.orders — ...
```

要求：

- 每条带来源/标签；
- SQL 模板截断，不能把整段长 SQL 无上限塞入；
- 总注入 ≤ 400 tokens；
- 去重后按分数排序；
- 检索失败不显示空标题。

### 8.3 防污染

- 不把检索内容写回 `conversation` 历史；
- `enhance_user_messages` 每次重建请求时临时注入，不持久化；
- 工具返回不放长文档；
- 记忆内容变更后，按 version 失效旧缓存。

### 8.4 DoD

- [ ] 自定义 enhancer 接入 `build_agent`
- [ ] 检索失败时 prompt 与 Stage 2 一致
- [ ] 注入内容不进入历史消息
- [ ] 单问题 token 可测量

---

## 9. T3-6：幂等导入与版本化

### 9.1 去重键

| 类型 | 去重键 |
|---|---|
| 文本记忆 | `(content_hash, source, version)` |
| 工具记忆 | `(normalized_sql_hash, tool_name)` |

`normalized_sql_hash`：

```text
去尾分号 → 压缩空白 → 统一大小写关键字 → sha256
```

### 9.2 导入流程

```text
读取 YAML
  → schema 校验
  → 计算 hash
  → 与 manifest 比对
  → dry-run 输出新增/更新/跳过
  → 正式 upsert
  → 写 manifest
```

### 9.3 版本与回滚

- 种子数据放 git；
- `manifest.yaml` 记录版本、条数、hash、embedding 模型；
- 每次导入写 `imported_at`；
- 支持按 `source` / `version` 删除；
- 禁止手工改向量库，必须走脚本。

### 9.4 DoD

- [ ] 连续执行两次导入，第二次新增 0
- [ ] dry-run 不改库
- [ ] 可列出某版本记忆
- [ ] 可按 source/version 回滚
- [ ] 导入失败不留下半成品

---

## 10. 验证与 G3

### 10.1 离线检索评测

新增 `analytics_agent/evaluation/retrieval/cases.yaml`：

```yaml
- id: "R-B02"
  query: "近 30 天各品类净收入"
  relevant_tables: ["orders", "order_items", "products", "refunds"]
  relevant_memory_ids: ["net_revenue.category_base"]
  relevant_sql_ids: ["b02_category_net_revenue"]
```

指标：

| 指标 | 定义 |
|---|---|
| `table_recall@5` | top-5 候选表覆盖 relevant_tables 的比例，按 query 平均 |
| `knowledge_recall@5` | top-5 文本记忆是否命中 relevant_memory_ids |
| `sql_hit@5` | top-5 工具记忆是否命中 relevant_sql_ids |
| `recall@5` | 上述三项按约定权重平均；主指标取 table + knowledge |

建议主验收：

```text
table_recall@5 ≥ 0.90
knowledge_recall@5 ≥ 0.90
sql_hit@5 ≥ 0.80（观察项）
```

### 10.2 端到端 A/B

```bash
# 1) 当前代码、关闭检索（control）
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml

# 2) 当前代码、开启检索
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/v1_30.yaml
```

记录：

- EX / first_try / behavior
- 平均工具轮次 / P95
- 平均 prompt tokens
- 检索命中率
- 失败 taxonomy

### 10.3 G3 门禁

- [ ] recall@5 ≥ 0.9
- [ ] EX ≥ 80%
- [ ] token 不高于 Stage 2
- [ ] 隔离/幂等/降级测试通过
- [ ] 两次同配置差异 ≤ 5pt（沿用 Stage 2 的稳定性要求）

### 10.4 失败处理

| 现象 | 优先检查 |
|---|---|
| 检索到无关知识 | embedding / BM25 权重 / 阈值 |
| 该召回没召回 | 种子缺失、问法覆盖不足、候选表漏表 |
| token 上涨 | 注入预算、候选表数量、L1 是否仍全量 |
| EX 下降 | 错误记忆污染、SQL 模板过时、注入位置不当 |
| 串用户 | metadata 过滤未生效 |

---

## 11. 每日步骤（D1~D6）

### D1：后端与最小检索（约 4 小时）

1. 实现 `ChromaMemory` 包装层
2. 固定中文 embedding
3. 写入 3 条文本记忆 + 1 条工具记忆
4. 手工检索 smoke

**DoD**：重启后能检索到；用户过滤生效。

### D2：种子数据（约 4 小时）

1. 整理 9 个指标 + 修正口径
2. 覆盖 22 条 answer 的工具记忆
3. 每条 3~5 个问法
4. 人工审核标记

**DoD**：knowledge ≥20 条，tool memory ≥15 条。

### D3：幂等导入（约 3 小时）

1. schema 校验
2. hash / manifest
3. dry-run
4. 二次导入新增 0

**DoD**：导入可重复、可回滚。

### D4：混合检索（约 4 小时）

1. BM25 中文分词
2. 向量召回
3. 合并/去重/rerank
4. 参数配置化

**DoD**：离线 recall@5 可计算。

### D5：候选表与注入（约 4 小时）

1. 候选表召回
2. 自定义 enhancer
3. token 预算
4. 失败降级

**DoD**：候选表不漏关键表；检索失败 prompt 不变。

### D6：G3 评测（约 3 小时）

1. control run
2. retrieval run
3. recall@5 / EX / token 对比
4. 填 §10 与执行日志

**DoD**：G3 门禁逐项有结论。

---

## 12. 风险与回滚

| 风险 | 影响 | 应对 |
|---|---|---|
| 错误记忆被复用 | 错得更稳定 | 入库审核；版本化；一键关闭检索 |
| embedding 模型不一致 | 检索到但语义无关 | 固定模型版本；collection 重建流程 |
| 候选表漏表 | 直接答错 | 指标关联表强制加入；低置信回退全量 |
| token 膨胀 | 成本上升、注意力稀释 | 注入预算；候选表替代全量 L1 |
| 用户串数据 | 隐私/合规事故 | `user_id`/`tenant_id` 强制过滤；单测 |
| 检索不可用 | 主流程中断 | 空结果 + 原 prompt 降级 |
| 种子与文档漂移 | 口径过期 | 种子 source/version；口径变更触发重建 |

回滚开关：

```text
MEMORY_ENABLED=0        # 关闭检索，回到 Stage 2 行为
MEMORY_BACKEND=chroma
MEMORY_COLLECTION=...
MEMORY_EMBEDDING=...
```

---

## 13. 执行日志（每完成一步追加）

| 日期 | 步骤 | 操作 | 结果 | 备注 |
|---|---|---|---|---|
| 2026-10-28 | Stage 3 启动 | 创建执行手册 | 完成 | 基线 v8；T3-1 待开始 |

---

## 附录 A：现有接口与模型

```python
class AgentMemory(ABC):
    save_tool_usage(...)
    save_text_memory(...)
    search_similar_usage(...)
    search_text_memories(...)
    get_recent_memories(...)
    get_recent_text_memories(...)
    delete_by_id(...)
    delete_text_memory(...)
    clear_memories(...)
```

```python
class ToolMemory(BaseModel):
    memory_id: Optional[str]
    question: str
    tool_name: str
    args: Dict[str, Any]
    timestamp: Optional[str]
    success: bool
    metadata: Optional[Dict[str, Any]]
    user_id: Optional[str]
    tenant_id: Optional[str]
```

```python
class TextMemory(BaseModel):
    memory_id: Optional[str]
    content: str
    timestamp: Optional[str]
    user_id: Optional[str]
    tenant_id: Optional[str]
```

## 附录 B：种子 YAML 示例

```yaml
# knowledge.yaml
- id: "retention.natural_day"
  text: "7 日留存按自然日 D+1~D+7；不是注册时间 +24h。"
  tags: ["retention_d7", "D-17"]
  source: "glossary.yaml#retention_d7"
  version: 1
```

```yaml
# tool_memories.yaml
- id: "b02_category_net_revenue"
  question: "近 30 天各品类的净收入是多少？"
  paraphrases:
    - "按品类看最近一个月的净收入"
    - "各品类扣退款后的收入"
  tool_name: "run_sql"
  sql: |
    SELECT ...
  tables: ["orders", "order_items", "products", "refunds"]
  metrics: ["net_revenue"]
  source: "v1_30.yaml#B-02"
  version: 1
  reviewed: true
```

## 附录 C：检索评测 case 示例

```yaml
- id: "R-B02"
  query: "近 30 天各品类净收入"
  relevant_tables: ["orders", "order_items", "products", "refunds"]
  relevant_memory_ids: ["net_revenue.category_base"]
  relevant_sql_ids: ["b02_category_net_revenue"]
```

## 附录 D：测试清单

- [ ] `ChromaMemory` 持久化
- [ ] 中文 query 召回
- [ ] `user_id` / `tenant_id` 隔离
- [ ] 空结果 / 后端异常降级
- [ ] seed schema 校验
- [ ] 二次导入幂等
- [ ] dry-run 不改库
- [ ] BM25 中文分词
- [ ] 向量召回可复现
- [ ] 去重后不重复注入
- [ ] 候选表不漏关键表
- [ ] 候选表失败回退全量
- [ ] enhancer 注入不写历史
- [ ] token 预算生效
- [ ] recall@5 可计算
- [ ] 回滚开关关闭检索
