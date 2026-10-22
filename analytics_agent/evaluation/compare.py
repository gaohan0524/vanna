"""Result-set equivalence comparison.

比较的是“结果集”，不是 SQL 字符串：等价 SQL 写法太多，字符串匹配会严重低估。

判定顺序：
1. 形状（行数 × 列数）
2. 列名集合（可用 `column_alias` 显式声明等价别名）
3. 行内容（默认行序无关；数值按相对容差比较）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pandas as pd

from .normalize import normalize


@dataclass
class CompareResult:
    """Outcome of one result-set comparison."""

    equal: bool
    reason: str  # ok | shape | columns | values
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - 便利语法
        return self.equal


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sort_key(value: Any) -> str:
    """Stable string key for sorting rows.

    数值统一用定点格式，保证 ``1`` 与 ``1.0`` 排序位置一致。
    """
    if value is None:
        return "\x00NULL"
    if _is_number(value):
        return f"{float(value):.8f}"
    return str(value)


def _signature(series: pd.Series) -> tuple:
    """Value multiset signature of one column (used for name-insensitive matching)."""
    return tuple(sorted(_sort_key(value) for value in series.tolist()))


def _match_columns_by_values(
    golden: pd.DataFrame, actual: pd.DataFrame
) -> Optional[dict[str, str]]:
    """Map actual columns to golden ones purely by their value multisets.

    列别名（``cnt`` vs ``order_count``）不影响语义正确性，严格按名称比较会把
    正确答案判错。这里用“整列取值集合相同”做确定性配对；匹配不上则返回 None。
    """
    unused = list(actual.columns)
    mapping: dict[str, str] = {}
    for column in golden.columns:
        signature = _signature(golden[column])
        for candidate in unused:
            if _signature(actual[candidate]) == signature:
                mapping[candidate] = column
                unused.remove(candidate)
                break
        else:
            return None
    return mapping if not unused else None


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    keys = [
        tuple(_sort_key(value) for value in row)
        for row in df.itertuples(index=False, name=None)
    ]
    order = sorted(range(len(keys)), key=keys.__getitem__)
    return df.iloc[order].reset_index(drop=True)


def _apply_alias(df: pd.DataFrame, alias: Optional[Mapping[str, str]]) -> pd.DataFrame:
    """Rename actual columns to golden names.

    ``alias`` 的方向是 ``{golden_name: actual_name}``，与用例里的
    ``column_alias: {"gmv": "total_gmv"}`` 一致。
    """
    if not alias:
        return df
    reverse = {actual: golden for golden, actual in alias.items()}
    return df.rename(columns=reverse)


def results_equal(
    golden: pd.DataFrame,
    actual: pd.DataFrame,
    *,
    rel_tol: float = 1e-6,
    order_sensitive: bool = False,
    column_alias: Optional[Mapping[str, str]] = None,
    column_match: str = "name",
) -> CompareResult:
    """Compare two result sets with column/row/float tolerance rules.

    Args:
        column_match: ``name`` 要求列名一致；``values`` 允许列别名不同，
            改为按整列取值集合做确定性配对。
    """
    g = normalize(golden)
    a = _apply_alias(normalize(actual), column_alias)

    if g.shape != a.shape:
        return CompareResult(False, "shape", f"golden={g.shape} actual={a.shape}")

    if set(g.columns) != set(a.columns):
        if column_match != "values":
            return CompareResult(
                False,
                "columns",
                f"golden={list(g.columns)} actual={list(a.columns)}",
            )
        mapping = _match_columns_by_values(g, a)
        if mapping is None:
            return CompareResult(
                False,
                "columns",
                f"golden={list(g.columns)} actual={list(a.columns)} (value mapping failed)",
            )
        a = a.rename(columns=mapping)

    a = a[list(g.columns)]

    if order_sensitive:
        g_rows, a_rows = g, a
    else:
        g_rows, a_rows = _sorted(g), _sorted(a)

    for index in range(len(g_rows)):
        for column in g_rows.columns:
            expected = g_rows.iat[index, g_rows.columns.get_loc(column)]
            observed = a_rows.iat[index, a_rows.columns.get_loc(column)]

            if _is_number(expected) and _is_number(observed):
                if not math.isclose(
                    float(expected), float(observed), rel_tol=rel_tol, abs_tol=1e-9
                ):
                    return CompareResult(
                        False,
                        "values",
                        f"row {index} col {column}: {expected!r} != {observed!r}",
                    )
                continue

            if expected != observed:
                return CompareResult(
                    False,
                    "values",
                    f"row {index} col {column}: {expected!r} != {observed!r}",
                )

    return CompareResult(True, "ok")
