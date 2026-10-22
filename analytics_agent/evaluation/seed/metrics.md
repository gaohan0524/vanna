# 指标口径卡片（单一事实源）

> 本文件是 prompt、指标工具与 golden SQL 的共同依据。修改口径必须先改这里，再改用例。
> 当前内容由业务负责人（方案负责人）确认后生效。

| 指标 | 定义 | 计算口径（针对本库） | 易错点 |
|---|---|---|---|
| GMV | 成交总额 | `orders` 中 `status IN ('paid','shipped','completed')` 且 `currency='CNY'`、`is_test=0` 的 `amount_cents` 之和，**不扣退款** | 含 USD、含取消单、含测试账号 |
| 净收入 | GMV − 退款 | 减去 `refunds.status='approved'` 的金额 | 忘记退款状态过滤 |
| 客单价 AOV | GMV / 有效订单数 | 分母与 GMV 同口径 | 分母用了全量订单 |
| 支付金额 | 实际到账 | `payments.status='succeeded' AND is_retry=0` 的 `amount_cents` 之和 | 重复流水 |
| 复购率 | 期内下单 ≥ 2 次的用户占比 | 按 `orders` 去重用户后统计 | 只统计首单用户；未限定时间范围 |
| 次日留存 | 当日新增用户次日有会话的比例 | `users.signup_at` 与 `sessions.started_at` 按天对齐 | 时区、日期边界 |
| 转化率 | 下单会话 / 总会话 | `sessions.converted=1` 占比 | 分子分母口径不一致 |
| MRR | 月度经常性收入 | `accounts.status='active'` 的 `mrr_cents` 之和 | 含 paused/churned |
| 渠道归因 | 订单归因到首触渠道 | 本库 `orders.channel` 即首触渠道 | 误用 `events` 做末触归因 |

## D2-0 待裁决项（确认后删除本节）

| # | 问题 | 建议口径 | 状态 |
|---|---|---|---|
| 1 | 复购率的统计窗口 | 近 90 天，按 `orders.created_at`，仅有效订单 | 待确认 |
| 2 | 渠道归因 | 首触：`orders.channel` 即首触渠道 | 待确认 |
| 3 | 退款计入哪个月 | 按 `refunds.created_at`（发生月）扣减 | 待确认 |
| 4 | “有效订单”范围 | `paid / shipped / completed` | 待确认 |
| 5 | 混合币种 | 经营指标只看 `CNY` | 待确认 |

> 这 5 项同时决定 E 类用例的标准答案，确认后请更新上表并在 `docs/STAGE0-EVAL.md` 附录 D 记录。

## 全局约定

1. 金额源字段单位是「分」，展示时 `/100.0`，保留 2 位小数。
2. 经营类查询默认排除 `is_test=1` 与 `deleted_at IS NOT NULL`。
3. 时间统一按 `Asia/Shanghai` 解释；分区字段为 `event_date`/`toDate(created_at)` 等。
4. 口径不唯一时（如“复购率”未给时间窗口），应先澄清而不是猜。
