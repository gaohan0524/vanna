"""Unit tests for schema rendering and optional annotations loading."""

from __future__ import annotations

from analytics_agent.context.render import estimate_tokens, render_schema
from analytics_agent.context.schema_catalog import load_annotations


def _catalog() -> dict:
    return {
        "version": 1,
        "database": "shop",
        "tables": [
            {
                "name": "users",
                "summary": "用户表",
                "columns": [
                    {"name": "user_id", "type": "UInt64", "comment": "用户 ID"},
                ],
            },
            {
                "name": "orders",
                "summary": "订单主表",
                "columns": [
                    {"name": "order_id", "type": "UInt64", "comment": "订单 ID"},
                    {
                        "name": "status",
                        "type": "LowCardinality(String)",
                        "comment": "订单状态",
                        "enum": ["paid", "shipped", "completed"],
                    },
                    {"name": "note", "type": "String", "comment": ""},
                ],
            },
        ],
    }


def test_render_orders_before_users_and_includes_enum():
    text = render_schema(_catalog())

    assert text.index("shop.orders") < text.index("shop.users")
    assert "订单主表" in text
    assert "取值: paid/shipped/completed" in text
    assert text.count("## 数据表") == 1


def test_render_skips_empty_comment():
    text = render_schema(_catalog())

    assert "- note String\n" in text or "- note String\r\n" in text
    assert "- note String —" not in text


def test_render_degrades_when_over_budget():
    catalog = _catalog()
    catalog["tables"][1]["columns"][0]["comment"] = "很长的注释" * 200

    text = render_schema(catalog, max_chars=200)

    assert "已截断" in text or len(text) <= 220


def test_estimate_tokens():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_load_annotations_missing_file_is_empty(tmp_path):
    assert load_annotations(tmp_path / "missing.yaml") == {}


def test_load_annotations_reads_tables(tmp_path):
    path = tmp_path / "annotations.yaml"
    path.write_text(
        "tables:\n  orders:\n    summary: 订单\n    columns:\n      status: 状态\n",
        encoding="utf-8",
    )

    notes = load_annotations(path)

    assert notes["orders"]["summary"] == "订单"
    assert notes["orders"]["columns"]["status"] == "状态"
