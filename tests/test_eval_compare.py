"""Unit tests for the Stage 0 result-set comparison (no database required)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd

from analytics_agent.evaluation.compare import results_equal
from analytics_agent.evaluation.normalize import normalize


def test_row_order_is_ignored_by_default():
    golden = pd.DataFrame({"channel": ["a", "b"], "gmv": [1.0, 2.0]})
    actual = pd.DataFrame({"channel": ["b", "a"], "gmv": [2.0, 1.0]})

    assert results_equal(golden, actual).equal


def test_order_sensitive_comparison():
    golden = pd.DataFrame({"month": ["2026-01", "2026-02"], "v": [1, 2]})
    actual = pd.DataFrame({"month": ["2026-02", "2026-01"], "v": [2, 1]})

    assert not results_equal(golden, actual, order_sensitive=True).equal


def test_numeric_tolerance():
    golden = pd.DataFrame({"v": [1.0]})
    close = pd.DataFrame({"v": [1.0 + 1e-9]})
    far = pd.DataFrame({"v": [1.001]})

    assert results_equal(golden, close).equal
    result = results_equal(golden, far)
    assert not result.equal
    assert result.reason == "values"


def test_shape_and_column_mismatch():
    golden = pd.DataFrame({"a": [1, 2], "b": [3, 4]})

    # 行数不同 → shape
    assert results_equal(golden, pd.DataFrame({"a": [1], "b": [3]})).reason == "shape"
    # 列缺失 / 列名不同 → columns
    assert results_equal(golden, pd.DataFrame({"a": [1, 2]})).reason == "columns"
    assert (
        results_equal(golden, pd.DataFrame({"a": [1, 2], "c": [3, 4]})).reason
        == "columns"
    )


def test_projection_allows_extra_columns_and_value_alias():
    golden = pd.DataFrame({"status": ["paid", "pending"], "cnt": [2, 1]})
    actual = pd.DataFrame(
        {
            "status": ["paid", "pending"],
            "order_count": [2, 1],
            "pct": [66.7, 33.3],
        }
    )

    comparison = results_equal(golden, actual, column_match="projection")
    assert comparison.equal, comparison.detail

    wrong = pd.DataFrame(
        {"status": ["paid", "pending"], "order_count": [2, 9], "pct": [1, 2]}
    )
    assert not results_equal(golden, wrong, column_match="projection").equal


def test_column_alias():
    golden = pd.DataFrame({"gmv": [10.0]})
    actual = pd.DataFrame({"total_gmv": [10.0]})

    assert not results_equal(golden, actual).equal
    assert results_equal(golden, actual, column_alias={"gmv": "total_gmv"}).equal


def test_column_match_by_values_allows_alias_and_reorder():
    golden = pd.DataFrame({"status": ["paid", "pending"], "cnt": [2, 1]})
    aliased = pd.DataFrame({"status": ["paid", "pending"], "order_count": [2, 1]})
    reordered = pd.DataFrame({"order_count": [2, 1], "status": ["paid", "pending"]})

    assert not results_equal(golden, aliased).equal
    assert results_equal(golden, aliased, column_match="values").equal
    assert results_equal(golden, reordered, column_match="values").equal


def test_column_match_by_values_still_detects_wrong_values():
    golden = pd.DataFrame({"status": ["paid", "pending"], "cnt": [2, 1]})
    actual = pd.DataFrame({"status": ["paid", "pending"], "order_count": [2, 9]})

    result = results_equal(golden, actual, column_match="values")

    assert not result.equal
    assert result.reason in {"values", "columns"}


def test_normalize_handles_decimal_nan_and_datetime():
    frame = pd.DataFrame(
        {
            "Amount": [Decimal("1.2300000"), None],
            "When": [datetime(2026, 1, 2, 3, 4, 5), None],
            "Flag": [True, False],
        }
    )

    normalized = normalize(frame)

    assert list(normalized.columns) == ["amount", "when", "flag"]
    assert normalized["amount"].tolist() == [1.23, None]
    assert normalized["when"].tolist() == ["2026-01-02 03:04:05", None]
    assert normalized["flag"].tolist() == [1, 0]
