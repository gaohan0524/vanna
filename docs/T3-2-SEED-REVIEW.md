# T3-2 种子审核清单（2026-10-29）

> 用途：逐条审核 `knowledge.yaml` 和 `tool_memories.yaml`。
> 审核前所有条目都是 `reviewed: false`；只有你确认后才能改为 `true`。
> 本清单不改数据，只提供核对点。

## 0. 怎么回复

对每条回复以下之一：

```text
ID: 通过
ID: 修改 -> 具体改成什么
ID: 删除
```

也可以只回复有问题的条目，其余默认按“待确认”保留。

## 1. 审核优先级

先看高风险项，再看普通项：

```text
B-02 品类净收入
B-05 复购率
D-18 加购下单
C-03 / C-15 MTD
A-09 MRR
```

这些项的文本或 SQL 如果改错，会直接污染检索和后续 EX。

## 2. 文本记忆（38 条）

> 原文来自 `analytics_agent/memory/seeds/knowledge.yaml`。
> “建议”不是自动通过，只是建议核对的重点。

### A. 指标定义与口径

| ID | 原文 | 来源 | 审核重点 | 建议 |
|---|---|---|---|---|
| text:GMV定义 | GMV 是有效订单金额之和：status 为 paid/shipped/completed，currency='CNY'，is_test=0；不扣退款。 | metrics.md#GMV | CNY、有效状态、测试过滤、不扣退款 | 核对后决定 |
| text:有效订单 | 有效订单只含 paid、shipped、completed；pending、cancelled、refunded 不计入 GMV/订单类指标。 | metrics.md#有效订单范围 | paid/shipped/completed；不含 pending/cancelled/refunded | 核对后决定 |
| text:客单价分母 | 整体客单价 AOV = GMV / 有效订单数；品类客单价用该品类商品金额 / 含该品类商品的订单数，不能套整体 GMV。 | metrics.md#客单价 | 整体 AOV 与品类 AOV 的分母不同 | 核对后决定 |
| text:支付金额口径 | 支付金额只统计 payments.status='succeeded' 且 is_retry=0 的金额；重试流水会重复计。 | metrics.md#支付金额 | succeeded + is_retry=0 | 核对后决定 |
| text:退款金额口径 | 退款金额只统计 refunds.status='approved'；退款是订单级，没有 product_id。 | metrics.md#退款金额 | approved；退款无 product_id | 核对后决定 |
| text:复购率窗口 | 复购率按 orders.created_at 近 90 天；只统计 CNY 有效订单。 | metrics.md#复购率 | 近 90 天、按 created_at、仅 CNY | 核对后决定 |
| text:复购率分母 | 复购率分母是有有效订单的用户；用户维度要排除 users.deleted_at，订单看 o.is_test=0。 | metrics.md#复购率 | 用户维度排注销；订单排测试 | 核对后决定 |
| text:支付转化率 | 支付转化率 = converted 的有效会话数 / 有效会话总数，输出 0~100 百分比；分母不是用户或全部会话。 | metrics.md#转化率 | 会话分母、输出百分比、不是用户分母 | 核对后决定 |
| text:转化率用户过滤 | 支付转化率要 JOIN users 排除 is_test=1 和 deleted_at 非空。 | metrics.md#转化率 | users.is_test=0 且 deleted_at 为空 | 核对后决定 |
| text:7日留存窗口 | 7 日留存按自然日 D+1~D+7：toDate(sessions.started_at) >= signup_date+1 且 < signup_date+8。 | glossary.yaml#retention_d7 | 自然日 D+1~D+7，含 D+7 | 核对后决定 |
| text:7日留存cohort | 7 日留存 cohort 必须满 7 天；近 N 个月取完整月，不含本月。 | STAGE0-EVAL.md#D-17 | cohort 满 7 天；近 N 月为完整月 | 核对后决定 |
| text:MRR账户过滤 | MRR 只统计 accounts.status='active'，并 JOIN users 排除 is_test=1 和 deleted_at 非空。 | metrics.md#MRR | accounts.status=active；join users 排测试/注销 | 核对后决定 |
| text:MRR不按用户去重 | MRR 是账户级 mrr_cents 之和，不按用户去重；paused/churned 不计入。 | metrics.md#MRR | 账户级求和；paused/churned 不计 | 核对后决定 |
| text:留存分母 | 7 日留存分母是 cohort 内注册用户数；先按用户去重，再统计是否在窗口内活跃。 | glossary.yaml#retention_d7 | cohort 内注册用户，按用户去重 | 核对后决定 |
| text:输出百分比 | 转化率、留存率、复购率等比例指标输出 0~100 的百分比，不是 0~1 小数。 | STAGE2-TOOLS.md#G2 | 比例指标输出 0~100，不是 0~1 | 核对后决定 |

### B. 全局过滤与字段

| ID | 原文 | 来源 | 审核重点 | 建议 |
|---|---|---|---|---|
| text:测试数据 | 经营指标统一排除 is_test=1 的订单、用户、会话与事件。 | metrics.md#全局约定 | 所有经营指标统一排 is_test=1 | 核对后决定 |
| text:软删口径 | 订单/收入/退款类按订单事实统计，不因用户注销剔除；用户维度（新增、留存、复购、国家转化）要排除 users.deleted_at 非空。 | metrics.md#全局约定 | 订单事实不排注销；用户维度排注销 | 核对后决定 |
| text:币种口径 | 经营指标默认只统计 CNY；USD 订单单独出题，不与 CNY 混合计算。 | metrics.md#混合币种 | 经营指标仅 CNY；USD 单独出题 | 核对后决定 |
| text:金额单位 | 金额字段单位是分，展示时除以 100.0，并保留两位小数。 | metrics.md#全局约定 | 分转元，保留两位小数 | 核对后决定 |
| text:渠道归因 | 本库的 orders.channel 就是首触渠道；不要用 events 做末触归因。 | metrics.md#渠道归因 | orders.channel 是首触渠道 | 核对后决定 |

### C. 时间窗口与边界

| ID | 原文 | 来源 | 审核重点 | 建议 |
|---|---|---|---|---|
| text:近N天 | 近 N 天指最近 N 个完整自然日，右端是 today 0 点；不要用滚动 24 小时窗口。 | metrics.md#全局约定 | 最近 N 个完整自然日；右端 today 0 点 | 核对后决定 |
| text:近N周 | 近 N 周指最近 N 个完整自然周，不含本周；周起点统一周一。 | metrics.md#全局约定 | 最近 N 个完整自然周；不含本周 | 核对后决定 |
| text:近N月 | 近 N 个月指最近 N 个完整自然月，不含本月；只有明确说本月至今才含当前月。 | metrics.md#全局约定 | 最近 N 个完整自然月；不含本月 | 核对后决定 |
| text:MTD右端 | 本月至今右端统一为 toStartOfDay(now())，排除今天未完成的数据。 | metrics.md#全局约定 | 右端 toStartOfDay(now()) | 核对后决定 |
| text:上月同期天数 | 上月同期用 dateDiff('day', 本月1号, 今天0点) 对齐自然日天数，不用 toDayOfMonth。 | metrics.md#全局约定 | 用 dateDiff 对齐自然日天数 | 核对后决定 |
| text:上月同期封顶 | 上月同期右边界用 least(addDays(上月1号, 天数), 本月1号) 封顶，防止月底溢出。 | metrics.md#全局约定 | least 封顶，防月底溢出 | 核对后决定 |
| text:周起点 | 任何周粒度都使用 toStartOfWeek(x, 1)，周一为起点；不要用 ClickHouse 默认周日。 | metrics.md#全局约定 | toStartOfWeek(x, 1)，周一 | 核对后决定 |
| text:左闭右开 | 时间窗口统一 >= start AND < end，并显式写出上界。 | metrics.md#全局约定 | 所有窗口 >= start AND < end | 核对后决定 |

### D. 高风险专项（B-02/D-18）

| ID | 原文 | 来源 | 审核重点 | 建议 |
|---|---|---|---|---|
| text:净收入公司级退款归属 | 公司级净收入按退款发生月扣减：refunds.created_at 在窗口内且 status='approved'。 | metrics.md#净收入 | 按 refunds.created_at 发生月扣减 | 核对后决定 |
| text:净收入品类级退款归属 | 品类净收入按订单 cohort：orders.created_at 圈定窗口，退款回溯到该订单所属期间。 | metrics.md#品类净收入 | 按订单 cohort，退款回溯到订单期间 | 核对后决定 |
| text:品类净收入基数 | 品类净收入以 orders.amount_cents（已扣优惠）为基数，不是 item_amount_cents 毛额。 | metrics.md#品类净收入 | 基数是 orders.amount_cents，不是 item 毛额 | 核对后决定 |
| text:品类分摊比例 | 订单净额按 item_amount_cents / 订单明细总额分摊到品类；item_amount 只作比例。 | metrics.md#品类净收入 | item_amount / 订单明细总额，只作比例 | 核对后决定 |
| text:退款分摊防放大 | 不能把订单级退款直接 JOIN 到多行 order_items 再 SUM；一个订单有 N 个商品会把退款放大 N 倍。 | metrics.md#品类净收入 | 禁止订单退款 JOIN 多行 item 后直接 SUM | 核对后决定 |
| text:加购漏斗不能复用支付转化率 | 加购→下单是 events 漏斗，不能复用 sessions.converted 的支付转化率。 | metrics.md#加购下单转化率 | events 漏斗与 sessions.converted 是两件事 | 核对后决定 |
| text:加购漏斗同会话时序 | 加购→下单要求同一 session 内 purchase 晚于 add_to_cart；当前 golden 以最早 purchase 与最早加购比较。 | STAGE0-EVAL.md#D-18 | 同一 session；最早 purchase 晚于最早加购 | 核对后决定 |
| text:加购漏斗过滤 | 加购→下单漏斗的会话要排除测试与注销用户，用户过滤放在 events 聚合前。 | STAGE0-EVAL.md#D-18 | events 聚合前排测试/注销用户 | 核对后决定 |

### E. SQL 写法与稳定性

| ID | 原文 | 来源 | 审核重点 | 建议 |
|---|---|---|---|---|
| text:空值和除零 | 聚合用 ifNull(SUM(...),0) 兜空；除法用 nullIf 防零。 | metrics.md#全局约定 | ifNull 兜空；nullIf 防除零 | 核对后决定 |
| text:TopN并列 | Top-N 商品按销量降序；并列时用商品 ID 做稳定排序，避免结果抖动。 | v1_30.yaml#B-11 | 销量降序；并列用 product_id 稳定排序 | 核对后决定 |

## 3. 工具记忆（21 条唯一 SQL）

> 每条 SQL 全文在 `analytics_agent/memory/seeds/tool_memories.yaml`；
> 这里列核对点，不重复粘贴长 SQL。
> `I-28` 已合并到 `tool:A01-GMV渠道`，用 `covers` 记录覆盖关系。

| ID | 标准问法 | 来源 | 覆盖 | 审核重点 |
|---|---|---|---|---|
| tool:A01-GMV渠道 | 最近 30 天各渠道的 GMV 是多少？ | v1_30.yaml#A-01 | A-01, I-28 | SQL 与 golden 一致；covers 覆盖 A-01 + I-28 |
| tool:A07-支付金额 | 近 30 天各支付方式的成功支付金额分别是多少？ | v1_30.yaml#A-07 | A-07 | payments/orders join、CNY、is_retry=0 |
| tool:A08-新增用户 | 近 7 天各渠道新增用户数是多少？ | v1_30.yaml#A-08 | A-08 | 近 7 个完整自然日；排测试/注销 |
| tool:A09-MRR套餐 | 当前各套餐的 MRR 是多少？（排除测试与注销用户） | v1_30.yaml#A-09 | A-09 | active、join users、排测试/注销、按 plan |
| tool:B02-品类净收入 | 近 30 天各品类的净收入是多少？（净收入 = 订单金额 − approved 退款，按订单内商品金额占比分摊到品类） | v1_30.yaml#B-02 | B-02 | 订单净额 × item 占比；防退款放大；高风险 |
| tool:B05-复购率 | 看一下近 90 天复购率（仅 CNY，排除测试与注销用户） | v1_30.yaml#B-05 | B-05 | 近 90 天、仅 CNY、排注销；高风险 |
| tool:B10-渠道退款 | 近 90 天各渠道的退款金额是多少？ | v1_30.yaml#B-10 | B-10 | approved；refunds.created_at 窗口 |
| tool:B11-商品销量Top10 | 近 30 天销量最高的 10 个商品是哪些？ | v1_30.yaml#B-11 | B-11 | 有效订单、CNY、Top10、product_id tie-break |
| tool:B12-国家支付转化率 | 近 30 天各国家用户的支付转化率是多少？ | v1_30.yaml#B-12 | B-12 | sessions/users、converted、排测试/注销 |
| tool:B13-品类客单价 | 近 30 天各品类的客单价（该品类收入 ÷ 含该品类商品的订单数）是多少？ | v1_30.yaml#B-13 | B-13 | 品类 item 金额 / 含该品类订单数 |
| tool:C03-GMV环比 | 本月至今的 GMV，跟上月同期比增长了多少？（截至今天 0 点，上月按相同自然日天数对齐） | v1_30.yaml#C-03 | C-03 | MTD、dateDiff 对齐、least 封顶 |
| tool:C14-周订单数 | 近 4 周每周的有效订单数（仅 CNY）是多少？ | v1_30.yaml#C-14 | C-14 | 周一为起点、最近 4 个完整周、仅 CNY |
| tool:C15-净收入环比 | 本月至今的净收入，跟上月同期比增长多少？（截至今天 0 点，上月按相同自然日天数对齐） | v1_30.yaml#C-15 | C-15 | 公司级退款按 refunds.created_at；MTD |
| tool:C16-月新增用户环比 | 近 12 个完整月（不含本月）每月新增用户数及环比是多少？（首月无环比） | v1_30.yaml#C-16 | C-16 | 近 12 个完整月、首月 NULL |
| tool:D04-上周支付转化率 | 上周各渠道的支付转化率是多少？（排除测试与注销用户） | v1_30.yaml#D-04 | D-04 | 上一个完整周、sessions/users |
| tool:D17-七日留存 | 近 3 个完整月（不含本月）每月注册用户的 7 日留存率（注册次日起 7 个自然日内有会话）是多少？ | v1_30.yaml#D-17 | D-17 | D+1~D+7 自然日、完整月 cohort |
| tool:D18-加购下单率 | 近 7 天加购后最终下单的会话比例是多少？（同一会话内，purchase 事件需晚于 add_to_cart；排除测试与注销用户） | v1_30.yaml#D-18 | D-18 | 同 session、最早 purchase 晚于最早加购、排测试/注销；高风险 |
| tool:H26-每日购买事件 | 近 7 天每天的 purchase 事件数是多少？ | v1_30.yaml#H-26 | H-26 | purchase、近 7 天、排测试 |
| tool:H27-事件分布 | 近 30 天各类事件的次数分布是怎样的？ | v1_30.yaml#H-27 | H-27 | 近 30 天、按 event_name |
| tool:I29-organic-GMV | 近 30 天 organic 渠道的 GMV 是多少？ | v1_30.yaml#I-29 | I-29 | organic、30 天、独立问法 |
| tool:I30-organic-GMV环比 | 近 30 天 organic 渠道 GMV 与再往前 30 天相比增长多少？ | v1_30.yaml#I-30 | I-30 | organic、前 30 天对比、单行单列 |

## 4. 审核后我会做的事

1. 只修改你明确回复的条目；
2. 通过项改 `reviewed: true`；
3. 修改项同步更新 `text` / `sql` / `source` / `version`；
4. 重跑 `tests/test_seed_data.py`；
5. 如果 SQL 变了，重新执行对应 SQL；
6. 更新 `manifest.yaml` 的 `status`、`reviewed_count`；
7. 更新 `STAGE3-RETRIEVAL.md` 和 `AGENT_QUALITY_PLAN.md` 状态。

## 5. 审核完成后的 T3-2 关闭条件

- [ ] 38 条文本全部有结论；
- [ ] 21 条工具 SQL 全部有结论；
- [ ] 通过的条目 `reviewed: true`；
- [ ] 修改项已重新校验；
- [ ] `manifest.status=reviewed`；
- [ ] 文档 DoD 勾选。