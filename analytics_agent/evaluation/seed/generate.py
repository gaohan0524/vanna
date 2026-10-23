"""Deterministic synthetic warehouse generator.

设计约束：
- 固定 `seed` + 固定锚点日期，任何机器生成同一份数据；
- 全部使用局部 `random.Random(seed)`，禁止全局 `random`；
- 数据截止到 `as_of`（默认昨天），评测用相对时间（`now() - INTERVAL 30 DAY`）；
- 金额用 Int64 存“分”。

用法：
    python -m analytics_agent.evaluation.seed.generate --scale 0.1
    python -m analytics_agent.evaluation.seed.generate --drop --as-of 2026-10-21
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import clickhouse_connect

from analytics_agent.config import Settings

SEED_DIR = Path(__file__).resolve().parent
TABLES = (
    "dim_date",
    "users",
    "products",
    "orders",
    "order_items",
    "payments",
    "refunds",
    "sessions",
    "events",
    "campaigns",
    "accounts",
    "meta_data_anchor",
)

CHANNELS = (("organic", 40), ("paid_search", 30), ("social", 20), ("referral", 10))
COUNTRIES = ("CN", "US", "JP", "SG")
CATEGORIES = ("electronics", "apparel", "grocery", "home", "beauty")
DEVICES = (("ios", 35), ("android", 40), ("web", 25))
ORDER_STATUS = (
    ("paid", 35),
    ("shipped", 30),
    ("completed", 25),
    ("pending", 5),
    ("cancelled", 3),
    ("refunded", 2),
)
EVENT_NAMES = (
    ("page_view", 60),
    ("add_to_cart", 25),
    ("checkout", 10),
    ("purchase", 5),
)
PAY_METHODS = (("alipay", 45), ("wechat", 40), ("card", 15))


def _weighted(rng: random.Random, pairs: Sequence[Tuple[str, int]]) -> str:
    total = sum(weight for _, weight in pairs)
    point = rng.random() * total
    upto = 0.0
    for value, weight in pairs:
        upto += weight
        if point <= upto:
            return value
    return pairs[-1][0]


def _ts(rng: random.Random, day: date, start_hour: int = 0) -> datetime:
    return datetime(
        day.year,
        day.month,
        day.day,
        rng.randint(start_hour, 23),
        rng.randint(0, 59),
        rng.randint(0, 59),
    )


def _connect(settings: Settings):
    user, password, database = settings.admin_credentials()
    return clickhouse_connect.get_client(
        host=settings.ch_host,
        port=settings.ch_port,
        username=user,
        password=password,
        database=database,
    )


def _execute_ddl(client, ddl_path: Path) -> None:
    script = ddl_path.read_text(encoding="utf-8")
    statements = [chunk.strip() for chunk in script.split(";") if chunk.strip()]
    for statement in statements:
        client.command(statement)


def _truncate(client, tables: Iterable[str]) -> None:
    for table in tables:
        client.command(f"TRUNCATE TABLE IF EXISTS shop.{table}")


def _drop(client, tables: Iterable[str]) -> None:
    for table in tables:
        client.command(f"DROP TABLE IF EXISTS shop.{table}")


def _insert(client, table: str, columns: Sequence[str], rows: Sequence[tuple]) -> None:
    if rows:
        client.insert(f"shop.{table}", list(rows), column_names=list(columns))


def generate(
    settings: Optional[Settings] = None,
    *,
    as_of: Optional[date] = None,
    seed: int = 42,
    scale: float = 1.0,
    drop: bool = False,
    days: int = 400,
) -> Dict[str, int]:
    """(Re)generate the whole synthetic warehouse and return row counts."""
    s = settings or Settings.from_env()
    anchor = as_of or (date.today() - timedelta(days=1))
    rng = random.Random(seed)
    client = _connect(s)

    # 锚点日 23:59:59：事实表时间戳一律不得越过它，否则会出现“未来数据”，
    # 让 now() 相关窗口把明天/下周的数据算进来。
    anchor_end = datetime.combine(anchor, time(23, 59, 59))

    def clamp(moment: datetime) -> datetime:
        return moment if moment <= anchor_end else anchor_end

    if drop:
        _drop(client, TABLES)
    _execute_ddl(client, SEED_DIR / "ddl.sql")
    if not drop:
        _truncate(client, TABLES)

    counts: Dict[str, int] = {}

    # ---------- dim_date ----------
    dim_rows = []
    for offset in range(-800, days + 1):
        day = anchor + timedelta(days=offset)
        is_weekend = 1 if day.weekday() >= 5 else 0
        dim_rows.append(
            (
                day,
                day.year,
                day.month,
                day.weekday() + 1,
                is_weekend,
                0,
            )
        )
    _insert(
        client,
        "dim_date",
        ["date_key", "year", "month", "day_of_week", "is_weekend", "is_holiday"],
        dim_rows,
    )
    counts["dim_date"] = len(dim_rows)

    # ---------- users ----------
    n_users = max(100, int(50_000 * scale))
    users: List[Tuple[int, datetime, str, str, str, int, Optional[datetime]]] = []
    for user_id in range(1, n_users + 1):
        signup_day = anchor - timedelta(days=rng.randint(0, 730))
        signup_at = _ts(rng, signup_day)
        is_test = 1 if rng.random() < 0.02 else 0
        deleted_at = (
            signup_at + timedelta(days=rng.randint(30, 300))
            if rng.random() < 0.01
            else None
        )
        # 软删日期不能晚于锚点（未来的“已删除”没有意义）
        if deleted_at is not None and deleted_at > anchor_end:
            deleted_at = None
        users.append(
            (
                user_id,
                signup_at,
                f"user{user_id}@example.com",
                _weighted(rng, CHANNELS),
                rng.choice(COUNTRIES),
                is_test,
                deleted_at,
            )
        )
    _insert(
        client,
        "users",
        [
            "user_id",
            "signup_at",
            "email",
            "channel",
            "country",
            "is_test",
            "deleted_at",
        ],
        users,
    )
    counts["users"] = len(users)

    # ---------- products ----------
    n_products = max(20, int(2_000 * scale))
    products: List[Tuple[int, str, str, str, Optional[str], int, int, int]] = []
    for product_id in range(1, n_products + 1):
        unit_price = rng.randint(1_000, 200_000)
        products.append(
            (
                product_id,
                f"SKU-{product_id:05d}",
                f"Product {product_id}",
                rng.choice(CATEGORIES),
                None if rng.random() < 0.02 else f"sub-{rng.randint(1, 5)}",
                unit_price,
                int(unit_price * rng.uniform(0.55, 0.8)),
                1 if rng.random() > 0.05 else 0,
            )
        )
    _insert(
        client,
        "products",
        [
            "product_id",
            "sku",
            "name",
            "category",
            "subcategory",
            "unit_price_cents",
            "cost_cents",
            "is_active",
        ],
        products,
    )
    counts["products"] = len(products)

    # ---------- campaigns ----------
    campaigns = []
    for campaign_id in range(1, 201):
        start_day = anchor - timedelta(days=rng.randint(0, 300))
        campaigns.append(
            (
                campaign_id,
                f"Campaign {campaign_id}",
                _weighted(rng, CHANNELS),
                start_day,
                start_day + timedelta(days=rng.randint(7, 90)),
                rng.randint(100_000, 50_000_000),
            )
        )
    _insert(
        client,
        "campaigns",
        ["campaign_id", "name", "channel", "start_date", "end_date", "budget_cents"],
        campaigns,
    )
    counts["campaigns"] = len(campaigns)

    # ---------- accounts ----------
    plans = (("starter", 50), ("pro", 35), ("enterprise", 15))
    mrr_by_plan = {"starter": 9_900, "pro": 49_900, "enterprise": 199_900}
    accounts = []
    for account_id in range(1, max(100, int(8_000 * scale)) + 1):
        user = users[rng.randrange(len(users))]
        plan = _weighted(rng, plans)
        status = "active" if rng.random() < 0.85 else rng.choice(("paused", "churned"))
        started_at = clamp(user[1] + timedelta(days=rng.randint(0, 30)))
        churned_at = (
            started_at + timedelta(days=rng.randint(30, 400))
            if status == "churned"
            else None
        )
        if churned_at is not None and churned_at > anchor_end:
            churned_at = None
        accounts.append(
            (
                account_id,
                user[0],
                plan,
                mrr_by_plan[plan],
                status,
                started_at,
                churned_at,
            )
        )
    _insert(
        client,
        "accounts",
        [
            "account_id",
            "user_id",
            "plan",
            "mrr_cents",
            "status",
            "started_at",
            "churned_at",
        ],
        accounts,
    )
    counts["accounts"] = len(accounts)

    # ---------- orders / items / payments / refunds ----------
    orders_per_day = max(1, int(900 * scale))
    total_orders = total_items = total_payments = total_refunds = 0
    order_id = 0
    payment_id = 0
    refund_id = 0

    for day_offset in range(days):
        day = anchor - timedelta(days=days - 1 - day_offset)
        order_rows = []
        item_rows = []
        payment_rows = []
        refund_rows = []

        for _ in range(orders_per_day):
            order_id += 1
            user = users[rng.randrange(len(users))]
            created_at = _ts(rng, day)
            if created_at < user[1]:
                created_at = user[1] + timedelta(minutes=rng.randint(1, 600))
            created_at = clamp(created_at)
            status = _weighted(rng, ORDER_STATUS)
            currency = "USD" if rng.random() < 0.05 else "CNY"
            channel = _weighted(rng, CHANNELS)
            campaign_id = rng.randint(1, 200) if rng.random() < 0.3 else None

            subtotal = 0
            for _ in range(rng.randint(1, 4)):
                product = products[rng.randrange(len(products))]
                quantity = rng.randint(1, 3)
                item_amount = product[5] * quantity
                subtotal += item_amount
                item_rows.append(
                    (order_id, product[0], quantity, product[5], item_amount)
                )

            discount = (
                min(subtotal - 100, int(subtotal * rng.uniform(0.0, 0.1)))
                if subtotal > 100
                else 0
            )
            amount = max(100, subtotal - discount)

            order_rows.append(
                (
                    order_id,
                    user[0],
                    created_at,
                    status,
                    currency,
                    amount,
                    discount,
                    channel,
                    campaign_id,
                    user[5],
                )
            )

            if status in ("paid", "shipped", "completed", "refunded"):
                payment_id += 1
                payment_rows.append(
                    (
                        payment_id,
                        order_id,
                        clamp(created_at + timedelta(minutes=rng.randint(0, 30))),
                        _weighted(rng, PAY_METHODS),
                        amount,
                        "succeeded",
                        0,
                    )
                )
                if rng.random() < 0.03:
                    payment_id += 1
                    payment_rows.append(
                        (
                            payment_id,
                            order_id,
                            clamp(created_at + timedelta(minutes=rng.randint(31, 90))),
                            _weighted(rng, PAY_METHODS),
                            amount,
                            "succeeded",
                            1,
                        )
                    )

            if status == "refunded" or rng.random() < 0.01:
                refund_id += 1
                refund_rows.append(
                    (
                        refund_id,
                        order_id,
                        clamp(created_at + timedelta(days=rng.randint(1, 20))),
                        int(amount * rng.uniform(0.3, 1.0)),
                        rng.choice(("quality", "late_delivery", "wrong_item", "other")),
                        "approved" if rng.random() < 0.8 else "pending",
                    )
                )

        _insert(
            client,
            "orders",
            [
                "order_id",
                "user_id",
                "created_at",
                "status",
                "currency",
                "amount_cents",
                "discount_cents",
                "channel",
                "campaign_id",
                "is_test",
            ],
            order_rows,
        )
        _insert(
            client,
            "order_items",
            [
                "order_id",
                "product_id",
                "quantity",
                "unit_price_cents",
                "item_amount_cents",
            ],
            item_rows,
        )
        _insert(
            client,
            "payments",
            [
                "payment_id",
                "order_id",
                "paid_at",
                "method",
                "amount_cents",
                "status",
                "is_retry",
            ],
            payment_rows,
        )
        _insert(
            client,
            "refunds",
            [
                "refund_id",
                "order_id",
                "created_at",
                "amount_cents",
                "reason",
                "status",
            ],
            refund_rows,
        )

        total_orders += len(order_rows)
        total_items += len(item_rows)
        total_payments += len(payment_rows)
        total_refunds += len(refund_rows)

    counts.update(
        orders=total_orders,
        order_items=total_items,
        payments=total_payments,
        refunds=total_refunds,
    )

    # ---------- sessions ----------
    n_sessions = max(100, int(400_000 * scale))
    session_rows = []
    session_index: List[Tuple[int, int, datetime]] = []
    for session_id in range(1, n_sessions + 1):
        user = users[rng.randrange(len(users))]
        day = anchor - timedelta(days=rng.randint(0, days - 1))
        started_at = _ts(rng, day)
        if started_at < user[1]:
            started_at = user[1] + timedelta(minutes=rng.randint(1, 600))
        started_at = clamp(started_at)
        session_rows.append(
            (
                session_id,
                user[0],
                started_at,
                _weighted(rng, CHANNELS),
                _weighted(rng, DEVICES),
                1 if rng.random() < 0.08 else 0,
            )
        )
        session_index.append((session_id, user[0], started_at))
        if len(session_rows) >= 50_000:
            _insert(
                client,
                "sessions",
                [
                    "session_id",
                    "user_id",
                    "started_at",
                    "channel",
                    "device",
                    "converted",
                ],
                session_rows,
            )
            session_rows = []
    _insert(
        client,
        "sessions",
        ["session_id", "user_id", "started_at", "channel", "device", "converted"],
        session_rows,
    )
    counts["sessions"] = n_sessions

    # ---------- events ----------
    n_events = max(100, int(1_000_000 * scale))
    event_rows = []
    for event_id in range(1, n_events + 1):
        session_id, user_id, started_at = session_index[
            rng.randrange(len(session_index))
        ]
        event_time = clamp(started_at + timedelta(seconds=rng.randint(0, 3_600)))
        name = _weighted(rng, EVENT_NAMES)
        if name == "page_view" and rng.random() < 0.1:
            properties = json.dumps({"event": "PageView"})
        else:
            properties = json.dumps({"event": name})
        event_rows.append((event_id, user_id, session_id, event_time, name, properties))
        if len(event_rows) >= 50_000:
            _insert(
                client,
                "events",
                [
                    "event_id",
                    "user_id",
                    "session_id",
                    "event_time",
                    "event_name",
                    "properties",
                ],
                event_rows,
            )
            event_rows = []
    _insert(
        client,
        "events",
        ["event_id", "user_id", "session_id", "event_time", "event_name", "properties"],
        event_rows,
    )
    counts["events"] = n_events

    # ---------- anchor ----------
    _insert(
        client,
        "meta_data_anchor",
        ["anchor_date", "seed", "generated_at"],
        [(anchor, seed, datetime.now())],
    )
    counts["meta_data_anchor"] = 1

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the synthetic shop warehouse"
    )
    parser.add_argument("--as-of", help="anchor date (YYYY-MM-DD), default: yesterday")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="row-count multiplier, e.g. 0.1 for a quick dev dataset",
    )
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--drop", action="store_true", help="drop and recreate tables")
    args = parser.parse_args()

    anchor = date.fromisoformat(args.as_of) if args.as_of else None
    counts = generate(
        as_of=anchor,
        seed=args.seed,
        scale=args.scale,
        drop=args.drop,
        days=args.days,
    )
    for table, rows in counts.items():
        print(f"{table:20s} {rows:>10,d}")


if __name__ == "__main__":
    main()
