"""`search_columns` 工具：把“业务词”翻译成候选字段。

定位：
- 只读 Stage 1 生成的 catalog，**不执行 SQL、不连数据库**；
- 解决“用户说 GMV/复购/转化率，但物理字段叫 amount_cents/user_id/converted”的落差；
- 返回的是“候选列 + 类型 + 注释”，让模型下一步用 describe_table 或 run_sql。

典型链路：
    不确定字段名 → search_columns("GMV") → describe_table("orders") → run_sql
"""

from __future__ import annotations

from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field
from vanna import Tool, ToolContext, ToolResult

from analytics_agent.context.search import search_columns


class SearchColumnsArgs(BaseModel):
    """参数收敛：关键词必填且限长，limit 限制在 1~50，防止模型一次拉全库。"""

    keyword: str = Field(
        min_length=1,
        max_length=50,
        description="业务关键词，如 GMV、渠道、支付时间、退款",
    )
    limit: int = Field(default=20, ge=1, le=50, description="最多返回多少列（1~50）")


class SearchColumnsTool(Tool[SearchColumnsArgs]):
    """按业务词搜索字段的工具。

    适用场景：
    - 不确定字段名（“渠道”到底是 channel 还是 source）；
    - 不确定某个指标由哪些字段计算（GMV 需要 amount_cents + status + currency）；
    - 需要在多张表之间找候选列。

    不适用：
    - 已经知道表、想看整张表结构 → describe_table；
    - 想确认枚举/日期格式 → sample_rows（T2-2）；
    - 想拿数据 → run_sql。
    """

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

        # “没搜到”不是执行失败：模型应该换词或去 describe_table，
        # 而不是原样重试同一个工具调用，所以这里返回 success=True + 操作建议。
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

            # 输出一行一列：表.字段 类型 -- 注释（注释是模型理解语义的主要依据）
            line = (
                f"{table.get('name')}.{column.get('name')}  {column.get('type')}"
                f"  -- {column.get('comment') or '无说明'}"
            )

            # 注释里已经写过“取值”时不再重复；否则补上枚举，省一次 sample_rows。
            enum = column.get("enum")
            if enum and "取值" not in (column.get("comment") or ""):
                line += f"；取值: {'/'.join(str(value) for value in enum)}"
            lines.append(line)

        return ToolResult(
            success=True,
            result_for_llm="\n".join(lines),
            # metadata 只用于审计/报告，不发给模型
            metadata={"hits": len(lines), "keyword": args.keyword},
        )
