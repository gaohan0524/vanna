"""Pure functions over the schema catalog (no database access)."""

from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 业务词 -> 字段/注释里的写法。没有同义词时，模型搜“GMV”会一无所获。
_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "gmv": ("amount_cents", "销售额", "成交额"),
    "销售额": ("gmv", "amount_cents"),
    "净收入": ("amount_cents", "refund", "退款"),
    "客单价": ("amount_cents", "order_id", "aov"),
    "复购": ("user_id", "order_id"),
    "留存": ("user_id", "signup_at", "started_at"),
    "转化率": ("converted", "session"),
    "退款": ("refund", "amount_cents"),
    "支付": ("payment", "paid_at", "amount_cents"),
    "渠道": ("channel",),
    "品类": ("category",),
    "国家": ("country",),
    "套餐": ("plan", "mrr"),
    "mrr": ("mrr_cents", "plan", "accounts"),
}


def _terms(keyword: str) -> set[str]:
    kw = keyword.strip().lower()
    if not kw:
        return set()
    terms = {kw}
    for key, aliases in _SYNONYMS.items():
        if kw == key or kw in key or key in kw:
            terms.update(alias.lower() for alias in aliases)
    return terms


def iter_columns(
    catalog: Dict[str, Any],
) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for table in catalog.get("tables", []):
        for column in table.get("columns", []):
            yield table, column


def search_columns(
    catalog: Dict[str, Any], keyword: str, limit: int = 20
) -> List[Dict[str, Any]]:
    """Search columns by table/column name, comment and table summary.

    Returns dicts with ``score``, ``table`` and ``column``, sorted by score desc.
    """
    terms = _terms(keyword)
    if not terms:
        return []

    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for table, column in iter_columns(catalog):
        table_name = str(table.get("name", "")).lower()
        column_name = str(column.get("name", "")).lower()
        comment = str(column.get("comment") or "").lower()
        summary = str(table.get("summary") or "").lower()

        score = 0
        for term in terms:
            if term == table_name:
                score += 6
            elif term in table_name:
                score += 3
            if term == column_name:
                score += 4
            elif term in column_name:
                score += 2
            if term in comment:
                score += 1
            if term in summary:
                score += 1

        if not score:
            continue
        key = (table_name, column_name)
        current = best.get(key)
        if current is None or score > current["score"]:
            best[key] = {"score": score, "table": table, "column": column}

    hits = list(best.values())
    hits.sort(
        key=lambda hit: (
            -hit["score"],
            hit["table"].get("name", ""),
            hit["column"].get("name", ""),
        )
    )
    return hits[: max(0, limit)]


def find_table(catalog: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    """Find a table by ``orders`` or ``shop.orders`` (case-insensitive)."""
    wanted = name.strip().lower()
    if "." in wanted:
        wanted = wanted.rsplit(".", 1)[-1]
    for table in catalog.get("tables", []):
        if str(table.get("name", "")).lower() == wanted:
            return table
    return None


def suggest_tables(catalog: Dict[str, Any], name: str, limit: int = 5) -> List[str]:
    tables = [str(table.get("name", "")) for table in catalog.get("tables", [])]
    wanted = name.strip().lower()
    close = difflib.get_close_matches(wanted, tables, n=limit, cutoff=0.5)
    if close:
        return close
    # 退化成“表名/表说明包含关键词”
    return [table for table in tables if wanted and (wanted in table.lower())][:limit]
