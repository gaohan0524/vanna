# 指标口径卡片（单一事实源）

> 本文件是 prompt、指标工具与 golden SQL 的共同依据。修改口径必须先改这里，再改用例。
> 当前内容由业务负责人（方案负责人）确认后生效。

| 指标 | 定义 | 计算口径（针对本库） | 易错点 |
|---|---|---|---|
| GMV | 成交总额 | `orders` 中 `status IN ('paid','shipped','completed')` 且 `currency='CNY'`、`is_test=0` 的 `amount_cents` 之和，**不扣退款** | 含 USD、含取消单、含测试账号 |
| 净收入 | GMV − 退款 | 减去 `refunds.status='approved'` 的金额；公司级同环比按退款发生月归属，品类级按订单 cohort（见下） | 忘记退款状态过滤；粒度不一致导致退款重复计 |
| 客单价 AOV | GMV / 有效订单数 | 分母与 GMV 同口径 | 分母用了全量订单 |
| 支付金额 | 实际到账 | `payments.status='succeeded' AND is_retry=0` 的 `amount_cents` 之和 | 重复流水 |
| 复购率 | 近 90 天下单 ≥ 2 次的用户占比（仅 CNY 有效订单） | 按 `orders.created_at` 圈定窗口，去重用户后统计 | 只统计首单用户；未限定时间范围/币种 |
| 次日留存 | 当日新增用户次日有会话的比例 | `users.signup_at` 与 `sessions.started_at` 按天对齐 | 时区、日期边界 |
| 7日留存 | 注册后 D+1~D+7 自然日内有会话的用户占比 | `users.signup_at` 与 `sessions.started_at` 按自然日对齐；cohort 必须满 7 天 | 时区、日期边界、观察窗不完整 |
| 转化率 | 下单会话 / 总会话（排除测试/软删） | `sessions.converted=1` 占比 | 分子分母口径不一致 |
| 加购→下单转化率 | 同会话 `add_to_cart` 后 `purchase` 的会话占比 | `events` 按 `session_id` 聚合；`purchase_time >= cart_time` | 不能复用支付转化率；必须同会话且有时序 |
| MRR | 月度经常性收入 | `accounts.status='active'` 且 join users 排测试/注销后的 `mrr_cents` 之和 | 含 paused/churned、未排测试或注销 |
| 渠道归因 | 订单归因到首触渠道 | 本库 `orders.channel` 即首触渠道 | 误用 `events` 做末触归因 |

## 口径裁决记录（2026-10-22 已确认）

| # | 问题 | 最终口径 | 状态 |
|---|---|---|---|
| 1 | 复购率的统计窗口 | 近 90 天，按 `orders.created_at`，仅 CNY 有效订单 | 已确认 |
| 2 | 渠道归因 | 首触：`orders.channel` 即首触渠道 | 已确认 |
| 3 | 退款计入哪个月 | 按 `refunds.created_at`（发生月）扣减，不按原订单月份 | 已确认 |
| 4 | “有效订单”范围 | `paid / shipped / completed`；不含 pending / cancelled / refunded | 已确认 |
| 5 | 混合币种 | 经营指标只看 `CNY`；USD 单独出题 | 已确认 |
| 6 | 品类净收入（B-02）退款归属 | 订单 cohort：按 `orders.created_at` 圈定窗口，退款回溯到该订单所属期间；按商品金额占比分摊 | 已确认（2026-10-23） |
| 7 | 公司级净收入（C-15）退款归属 | 按 `refunds.created_at` 发生月扣减 | 已确认（2026-10-22） |

> 这 5 项同时决定 E 类用例的标准答案；变更需记录日期并触发 golden 复核。

## 全局约定

1. 金额源字段单位是「分」，展示时 `/100.0`，保留 2 位小数。
2. 测试数据一律排除 `is_test=1`。软删口径分两类：
   - **订单/收入/退款类**按订单事实统计，不因用户注销剔除历史订单（不 join `deleted_at`）；
   - **用户维度**（新增、留存、复购、按国家转化）排除 `users.deleted_at IS NOT NULL`。
3. 时间统一按 `Asia/Shanghai` 解释；分区字段为 `event_date`/`toDate(created_at)` 等。
4. 口径不唯一时（如“复购率”未给时间窗口），应先澄清而不是猜。
5. 同环比默认「本月至今 vs 上月 1 日至同日」（MTD），不是整月对比整月。
6. **品类净收入（B-02）的口径**：
   - 以 `orders.created_at` 圈定时间窗口（订单 cohort），退款回溯到该订单所属期间；
   - 净收入基数是 **`orders.amount_cents`（已扣优惠）**，不是 `item_amount_cents` 毛额；
   - `item_amount_cents` 只用于**分摊比例**：先算订单净额 `order_net = amount_cents - approved_refund`，再按 `item_amount / order_item_total` 分摊到品类；
   - 禁止把订单级退款直接 JOIN 到多行商品上再 `SUM`——一个订单有 N 个商品，退款会被放大 N 倍并错分到多个品类。
7. 校验恒等式：`Σ 品类净收入 = 窗口内订单金额 − 这些订单的 approved 退款总额`（四舍五入误差 ≤ 0.1 元）。
8. 时间窗口统一左闭右开（`>= start AND < end`），并显式写出上界；聚合前用 `ifNull(SUM(...), 0)` 兜住空区间，除法用 `nullIf` 防零。
9. 「上月同期」的右边界必须用 `least(addDays(上月1号, toDayOfMonth(today())), 本月1号)` 封顶，否则 29~31 号跑时上界会溢出到下个月（12-31 → 12-02），把本月数据算进上月。
10. 「近 N 天」= **最近 N 个完整自然日**：`>= toStartOfDay(now()) - INTERVAL N DAY AND < toStartOfDay(now())`，不用滚动 24h 窗口（`now() - INTERVAL N DAY`）。
11. 任何周粒度/周窗口统一以**周一**为起点：`toStartOfWeek(x, 1)`；「上周」= 上一个完整自然周（周一~周日），不要用 ClickHouse 默认的周日。
12. 「本月至今」右端统一为 `toStartOfDay(now())`；「上月同期」天数用 `dateDiff('day', toStartOfMonth(today()), toStartOfDay(now()))`，右边界再用 `least(..., toStartOfMonth(today()))` 封顶。
13. 「近 N 个月」= 最近 N 个**完整自然月**（不含本月）；「近 N 周」= 最近 N 个完整自然周（不含本周）。只有明确说「本月至今」才包含当前月。
