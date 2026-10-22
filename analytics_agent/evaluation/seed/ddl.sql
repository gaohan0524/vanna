-- Stage 0 合成业务库 DDL（ClickHouse）
-- 金额一律用 Int64 存“分”，避免浮点误差污染评测比较。

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
    channel     LowCardinality(String),
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
    currency        LowCardinality(String),  -- 陷阱④：95% CNY，5% USD
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
    payment_id   UInt64,
    order_id     UInt64,
    paid_at      DateTime('Asia/Shanghai'),
    method       LowCardinality(String),
    amount_cents Int64,
    status       LowCardinality(String),
    is_retry     UInt8                     -- 陷阱⑤：重试流水，直接 sum 会重复计
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
    status        LowCardinality(String)   -- approved/rejected/pending
)
ENGINE = MergeTree
ORDER BY (toDate(created_at), refund_id);

CREATE TABLE IF NOT EXISTS shop.sessions
(
    session_id  UInt64,
    user_id     UInt64,
    started_at  DateTime('Asia/Shanghai'),
    channel     LowCardinality(String),
    device      LowCardinality(String),
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
    event_name  LowCardinality(String),
    properties  String                     -- JSON 字符串，陷阱⑥：大小写不统一
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
    plan        LowCardinality(String),
    mrr_cents   Int64,
    status      LowCardinality(String),
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
