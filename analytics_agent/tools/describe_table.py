"""`describe_table` 工具：一次看清一张表的完整结构。

定位：
- 只读 catalog，**不执行 SQL、不连数据库**；
- 比 `search_columns` 更“重”：返回整张表的字段、类型、注释、枚举、行数、引擎；
- 适合“已经知道表名，但需要确认字段/枚举/量级”的场景。

典型链路：
    用户问“按状态看订单” → describe_table("orders")
      → 看到 status 的枚举与 is_test 注释 → run_sql
"""

from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import BaseModel, Field
from vanna import Tool, ToolContext, ToolResult

from analytics_agent.context.search import find_table, suggest_tables


class DescribeTableArgs(BaseModel):
    """表名必填；允许带库名前缀（shop.orders），由 find_table 统一处理。"""

    table: str = Field(
        min_length=1,
        max_length=60,
        description="表名，可带库名，如 orders 或 shop.orders",
    )


class DescribeTableTool(Tool[DescribeTableArgs]):
    """描述单张表的结构。

    适用场景：
    - 已经确定表，但不确定字段名/类型；
    - 需要确认枚举取值（status、currency、channel）；
    - 需要知道表规模（行数）判断是否要先加时间过滤。

    不适用：
    - 还不知道表名 → search_columns；
    - 想确认真实数据格式/脏数据 → sample_rows；
    - 想拿查询结果 → run_sql。
    """

    def __init__(self, catalog: Dict[str, Any]) -> None:
        self._catalog = catalog

    @property
    def name(self) -> str:
        return "describe_table"

    @property
    def description(self) -> str:
        return (
            "describe_table：查看单张表的字段、类型、注释、枚举与行数。\n"
            "用：知道表名，但不确定字段、枚举或规模。\n"
            "不要用：不知道表名（先 search_columns）、看真实数据（用 sample_rows）、"
            "执行查询（用 run_sql）。\n"
            "分工：search_columns 找表 → describe_table 看结构 → sample_rows 看值。"
        )

    def get_args_schema(self) -> Type[DescribeTableArgs]:
        return DescribeTableArgs

    async def execute(
        self, context: ToolContext, args: DescribeTableArgs
    ) -> ToolResult:
        table = find_table(self._catalog, args.table)

        # 表名是硬参数：找不到就返回失败 + 候选表，让模型换表名重试。
        # 这里不能 success=True，否则模型可能把“未找到”当成“表是空的”。
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

        # 第一行给“规模 + 引擎”，帮助模型判断要不要加时间过滤 / LIMIT。
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

            # 枚举是模型最容易猜错的部分；注释里没写就补上。
            enum = column.get("enum")
            if enum and "取值" not in (column.get("comment") or ""):
                line += f"；取值: {'/'.join(str(value) for value in enum)}"
            lines.append(line)

        return ToolResult(
            success=True,
            result_for_llm="\n".join(lines),
            metadata={"table": name, "columns": len(columns)},
        )
