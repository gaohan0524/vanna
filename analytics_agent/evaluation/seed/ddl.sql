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
