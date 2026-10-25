"""Render the schema catalog into a compact prompt section (L1).

目标：把 12 张表压到约 2,200 tokens（≈8,800 字符），同时保留字段枚举与易错点。
超预算时按“去类型 → 截注释 → 硬截断”逐级降级。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from .schema_catalog import TABLE_ORDER

DEFAULT_MAX_CHARS = 8_800


def _order_key(name: str) -> tuple[int, str]:
    try:
        return (TABLE_ORDER.index(name), name)
    except ValueError:
        return (len(TABLE_ORDER), name)


def _ordered(tables: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return sorted(tables, key=lambda table: _order_key(table["name"]))


def _render(
    catalog: Dict[str, Any],
    *,
    show_type: bool,
    comment_limit: int | None,
) -> str:
    tables = _ordered(catalog.get("tables", []))
    database = catalog.get("database", "shop")
    blocks = [f"## 数据表（{database}，{len(tables)} 张）"]

    for table in tables:
        summary = (table.get("summary") or "").strip()
        header = f"### {database}.{table['name']}"
        if summary:
            header += f" — {summary}"
        lines = [header]

        for column in table.get("columns", []):
            line = f"- {column['name']}"
            if show_type:
                line += f" {column['type']}"
            comment = (column.get("comment") or "").strip()
            if comment_limit is not None and len(comment) > comment_limit:
                comment = comment[:comment_limit].rstrip() + "…"
            if comment:
                line += f" — {comment}"
            enum = column.get("enum")
            # 注释里已经列过取值时不再重复，避免同一行出现两个“取值”
            if enum and "取值" not in comment:
                line += f"；取值: {'/'.join(str(value) for value in enum)}"
            lines.append(line)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def render_schema(
    catalog: Dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Render catalog to prompt text, degrading gracefully over ``max_chars``."""
    text = _render(catalog, show_type=True, comment_limit=None)
    if len(text) <= max_chars:
        return text

    text = _render(catalog, show_type=False, comment_limit=30)
    if len(text) <= max_chars:
        return text

    text = _render(catalog, show_type=False, comment_limit=12)
    if len(text) <= max_chars:
        return text

    cut = text[:max_chars].rfind("\n")
    if cut <= 0:
        cut = max_chars
    return text[:cut] + "\n…（schema 已截断）"


def estimate_tokens(text: str) -> int:
    """Rough token estimate for Chinese/English mixed text."""
    return len(text) // 4
