"""HTML report should never truncate executed SQL."""

from __future__ import annotations

from analytics_agent.evaluation.report import build_payload, write_reports


def test_html_keeps_full_executed_sql_and_reason(tmp_path):
    long_sql = "SELECT " + "very_long_column_name, " * 40 + "count() FROM shop.orders"
    long_reason = "values differ: " + "detail, " * 60
    record = {
        "id": "X-01",
        "layer": "X",
        "expected_behavior": "answer",
        "passed": False,
        "first_try": False,
        "eventual": True,
        "taxonomy": "METRIC_WRONG",
        "tool_calls": 1,
        "latency_ms": 100,
        "prompt_chars": 200,
        "executed_sql": long_sql,
        "answer": "n/a",
        "reason": long_reason,
    }
    payload = build_payload(
        dataset_name="t",
        dataset_path="t.yaml",
        agent_info={},
        records=[record],
    )

    paths = write_reports(tmp_path, payload)
    text = paths["html"].read_text(encoding="utf-8")

    assert long_sql in text
    assert "very_long_column_name" in text
    assert long_reason in text
    assert "Eventual EX" in text
    assert payload["summary"]["eventual_ex"] == 1.0
    assert payload["summary"]["eventual_accuracy"] == 1.0
