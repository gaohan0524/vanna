"""Tool: search columns in the catalog by business keyword."""

from __future__ import annotations

from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field
from vanna import Tool, ToolContext, ToolResult

from analytics_agent.context.search import search_columns


class SearchColumnsArgs(BaseModel):
    keyword: str = Field(
        min_length=1,
        max_length=50,
        description="业务关键词，如 GMV、渠道、支付时间、退款",
    )
    limit: int = Field(default=20, ge=1, le=50, description="最多返回多少列（1~50）")


class SearchColumnsTool(Tool[SearchColumnsArgs]):
    def __init__(self, catalog: Dict[str, Any]) -> None:
        self._catalog = catalog

    @property
    def name(self) -> str:
        return "search_columns"

    @property
    def description(self) -> str:
        return (
            "按业务关键词查找字段。当你不确定字段名、不知道某个指标由哪些字段计算、"
            "或需要在多张表之间找候选列时调用。不要用它执行查询，也不要替代 describe_table。"
        )

    def get_args_schema(self) -> Type[SearchColumnsArgs]:
        return SearchColumnsArgs

    async def execute(
        self, context: ToolContext, args: SearchColumnsArgs
    ) -> ToolResult:
        hits: List[Dict[str, Any]] = search_columns(
            self._catalog, args.keyword, args.limit
        )
        if not hits:
            return ToolResult(
                success=True,
                result_for_llm=(
                    f"未找到与 '{args.keyword}' 相关的字段。"
                    "请换用业务词（如 GMV、销售额、退款、渠道），"
                    "或先用 describe_table 查看核心表。"
                ),
            )

        lines = []
        for hit in hits:
            table = hit["table"]
            column = hit["column"]
            line = (
                f"{table.get('name')}.{column.get('name')}  {column.get('type')}"
                f"  -- {column.get('comment') or '无说明'}"
            )
            enum = column.get("enum")
            if enum and "取值" not in (column.get("comment") or ""):
                line += f"；取值: {'/'.join(str(value) for value in enum)}"
            lines.append(line)

        return ToolResult(
            success=True,
            result_for_llm="\n".join(lines),
            metadata={"hits": len(lines), "keyword": args.keyword},
        )
