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
    golden = pd.DataFrame({"a": [1], "b": [2]})

    assert results_equal(golden, pd.DataFrame({"a": [1]})).reason == "shape"
    assert results_equal(golden, pd.DataFrame({"a": [1], "c": [2]})).reason == "columns"


def test_column_alias():
    golden = pd.DataFrame({"gmv": [10.0]})
    actual = pd.DataFrame({"total_gmv": [10.0]})

    assert not results_equal(golden, actual).equal
    assert results_equal(golden, actual, column_alias={"gmv": "total_gmv"}).equal


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
