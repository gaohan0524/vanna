"""Tool: describe one table from the catalog."""

from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import BaseModel, Field
from vanna import Tool, ToolContext, ToolResult

from analytics_agent.context.search import find_table, suggest_tables


class DescribeTableArgs(BaseModel):
    table: str = Field(
        min_length=1,
        max_length=60,
        description="表名，可带库名，如 orders 或 shop.orders",
    )


class DescribeTableTool(Tool[DescribeTableArgs]):
    def __init__(self, catalog: Dict[str, Any]) -> None:
        self._catalog = catalog

    @property
    def name(self) -> str:
        return "describe_table"

    @property
    def description(self) -> str:
        return (
            "查看单张表的完整字段、类型、注释、枚举取值与行数。"
            "当已经确定表、但不确定字段或取值时调用；不要用它执行查询，"
            "也不要在不知道表名时乱试（先用 search_columns）。"
        )

    def get_args_schema(self) -> Type[DescribeTableArgs]:
        return DescribeTableArgs

    async def execute(
        self, context: ToolContext, args: DescribeTableArgs
    ) -> ToolResult:
        table = find_table(self._catalog, args.table)
        if table is None:
            suggestions = suggest_tables(self._catalog, args.table)
            if suggestions:
                hint = "相近的表：" + "、".join(suggestions)
            else:
                hint = "请先用 search_columns 找到候选表。"
            return ToolResult(
                success=False,
                result_for_llm=f"未找到表 '{args.table}'。{hint}",
                error="unknown_table",
            )

        database = self._catalog.get("database", "shop")
        name = table.get("name", "")
        lines = [
            f"表：{database}.{name}（{int(table.get('rows', 0)):,} 行，"
            f"{table.get('engine') or 'unknown'}）"
        ]
        summary = (table.get("summary") or "").strip()
        if summary:
            lines.append(f"说明：{summary}")

        columns = table.get("columns") or []
        lines.append(f"字段（{len(columns)}）：")
        for column in columns:
            line = (
                f"- {column.get('name')} {column.get('type')}"
                f" — {column.get('comment') or '无说明'}"
            )
            enum = column.get("enum")
            if enum and "取值" not in (column.get("comment") or ""):
                line += f"；取值: {'/'.join(str(value) for value in enum)}"
            lines.append(line)

        return ToolResult(
            success=True,
            result_for_llm="\n".join(lines),
            metadata={"table": name, "columns": len(columns)},
        )
