"""Catalog 查询层：在 `schema_catalog.json` 上做纯内存检索，不连数据库。

为什么要单独一层：
- `search_columns` / `describe_table` 这两个工具只负责“参数校验 + 输出格式”；
- 真正的检索、打分、同义词展开放在这里，便于用 fixture 单测，不需要 ClickHouse；
- catalog 在启动时已加载，搜索是纯内存操作，零延迟、可重复、不会因为数据库抖动影响工具结果。

调用链：
    用户问题
      → search_columns（本模块 search_columns）
      → describe_table（本模块 find_table）
      → run_sql

维护提示：
- 新增指标或业务叫法时，优先更新 `_SYNONYMS`，不要改打分公式；
- 本模块不生成 SQL、不访问数据库，也不依赖 Settings。
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 业务词 -> 物理字段/注释里的写法。
# 背景：用户问“GMV”时，catalog 里只有 amount_cents，字段名和注释都不含“gmv”；
# 没有这层展开，模型会搜不到任何结果，转而猜字段。
# 这是“最小业务词表”，不是完整语义层；新增指标时同步补一行。
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
    """把用户输入展开成“原词 + 同义词”，统一小写后参与匹配。

    例：``gmv`` -> ``{"gmv", "amount_cents", "销售额", "成交额"}``。
    空字符串返回空集合，调用方据此直接返回空结果。
    """
    kw = keyword.strip().lower()
    if not kw:
        return set()

    terms = {kw}
    for key, aliases in _SYNONYMS.items():
        # 双向包含：输入“gmv”能命中 key；“销售额同比”也能命中“销售额”
        if kw == key or kw in key or key in kw:
            terms.update(alias.lower() for alias in aliases)
    return terms


def iter_columns(
    catalog: Dict[str, Any],
) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """按 catalog 顺序逐列迭代，返回 ``(table_dict, column_dict)``。

    工具层不关心 catalog 的嵌套结构，统一走这个生成器。
    """
    for table in catalog.get("tables", []):
        for column in table.get("columns", []):
            yield table, column


def search_columns(
    catalog: Dict[str, Any], keyword: str, limit: int = 20
) -> List[Dict[str, Any]]:
    """按业务词搜索字段，返回按相关度排序的命中。

    打分规则（数值仅用于排序，不代表概率）：
    - 表名精确命中 +6，表名包含 +3；
    - 字段名精确命中 +4，字段名包含 +2；
    - 字段注释包含 +1，表说明包含 +1。

    同一个字段被多个同义词命中时取最高分，不累加，避免长注释刷分。
    返回 ``[{"score": int, "table": dict, "column": dict}, ...]``。
    """
    terms = _terms(keyword)
    if not terms:
        return []

    # key 用 (table, column) 去重：同义词可能让同一列被多次命中。
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
    # 同分时按表名、字段名排序，保证同一输入多次调用结果稳定。
    hits.sort(
        key=lambda hit: (
            -hit["score"],
            hit["table"].get("name", ""),
            hit["column"].get("name", ""),
        )
    )
    return hits[: max(0, limit)]


def find_table(catalog: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    """按表名查找，兼容 ``orders`` 与 ``shop.orders``，大小写不敏感。

    只做精确匹配；拼错时由 ``suggest_tables`` 给候选，不要在这里做模糊匹配，
    避免“oder”直接返回“orders”而掩盖用户的输入错误。
    """
    wanted = name.strip().lower()
    if "." in wanted:
        # 去掉库名前缀，只比较表名
        wanted = wanted.rsplit(".", 1)[-1]
    for table in catalog.get("tables", []):
        if str(table.get("name", "")).lower() == wanted:
            return table
    return None


def suggest_tables(catalog: Dict[str, Any], name: str, limit: int = 5) -> List[str]:
    """为拼错的表名给候选：先做编辑距离，再退化为“表名包含关键词”。

    只在 ``describe_table`` 失败时调用，目的是让模型“换一个表名重试”，
    而不是原样重试同一个错误参数。
    """
    tables = [str(table.get("name", "")) for table in catalog.get("tables", [])]
    wanted = name.strip().lower()

    close = difflib.get_close_matches(wanted, tables, n=limit, cutoff=0.5)
    if close:
        return close

    # 编辑距离没有候选时，退化成子串匹配（例如输入“order”命中“orders”）
    return [table for table in tables if wanted and (wanted in table.lower())][:limit]
