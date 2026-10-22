"""DataFrame normalization before result-set comparison.

规则（与 `docs/STAGE0-EVAL.md` 7.3 一致）：
- 列名：strip + lower；
- 浮点：四舍五入到 6 位，消除 1e-15 级噪声；
- 日期/时间：统一成字符串；
- 空值：NaN / NaT / None 统一为 None；
- 布尔：统一为 0 / 1；
- Decimal / numpy 标量：转成 Python 原生类型。
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

FLOAT_DECIMALS = 6


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _canon(value: Any) -> Any:
    """Convert one cell into a canonical Python value."""
    if _is_missing(value):
        return None

    # numpy scalar -> python scalar
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return round(float(value), FLOAT_DECIMALS)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return round(value, FLOAT_DECIMALS)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Return a normalized copy of ``df`` (does not mutate the input).

    逐列用 ``dtype="object"`` 承载结果：否则 pandas 会把数值列里的 ``None``
    重新转成 ``NaN``，导致 NULL 与 NULL 在比较时互不相等。
    """
    out = df.copy()
    out.columns = [str(column).strip().lower() for column in out.columns]
    for column in out.columns:
        values = [_canon(value) for value in out[column].tolist()]
        out[column] = pd.Series(values, index=out.index, dtype="object")
    return out
