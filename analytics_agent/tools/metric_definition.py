"""`get_metric_definition` 工具：给出指标的唯一权威口径。

定位：
- 只读 `glossary.yaml`，**不查数据库**；
- 解决“GMV 要不要扣退款、净收入按哪个月、复购率窗口多长”这类口径歧义；
- 返回定义、公式、易错点，避免模型凭记忆编公式。

典型链路：
    用户问“净收入” → get_metric_definition("净收入") → 按公式写 SQL
"""

from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import Field
from vanna import Tool, ToolContext, ToolResult

from analytics_agent.context.glossary import (
    find_metric,
    list_metric_names,
    suggest_metrics,
)
from analytics_agent.tools.params import ToolArgs


class GetMetricDefinitionArgs(ToolArgs):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="指标名或别名，如 GMV、净收入、客单价、复购率、MRR",
    )


class GetMetricDefinitionTool(Tool[GetMetricDefinitionArgs]):
    """返回单个指标的定义、公式与易错点。

    适用：
    - 用户提到指标名，但模型不确定口径；
    - 多个指标容易混淆（GMV vs 净收入、支付金额 vs 退款金额）。

    不适用：
    - 查字段名 → search_columns；
    - 查表结构 → describe_table；
    - 拿数据 → run_sql。

    未命中时返回候选指标，**不编造定义**。
    """

    def __init__(self, glossary: Dict[str, Any]) -> None:
        self._glossary = glossary

    @property
    def name(self) -> str:
        return "get_metric_definition"

    @property
    def description(self) -> str:
        return (
            "get_metric_definition：查询业务指标的权威口径。\n"
            "用：不确定 GMV / 净收入 / 客单价 / 复购率等指标怎么算，或口径有歧义。\n"
            "不要用：查字段（用 search_columns）、查表（用 describe_table）、"
            "执行查询（用 run_sql）。\n"
            "分工：先确认口径 → 再写 SQL → run_sql。"
        )

    def get_args_schema(self) -> Type[GetMetricDefinitionArgs]:
        return GetMetricDefinitionArgs

    async def execute(
        self, context: ToolContext, args: GetMetricDefinitionArgs
    ) -> ToolResult:
        found = find_metric(self._glossary, args.name)

        if found is None:
            candidates = suggest_metrics(self._glossary, args.name)
            available = "、".join(list_metric_names(self._glossary))
            if candidates:
                hint = "候选：" + "、".join(candidates)
            else:
                hint = f"可用指标：{available}"
            return ToolResult(
                success=True,
                result_for_llm=(
                    f"未找到指标 '{args.name}'。{hint}\n"
                    "请从候选里选一个；不要自行编造公式。"
                ),
                metadata={"found": False, "query": args.name},
            )

        key, metric = found
        name = metric.get("name") or key
        short = metric.get("short") or metric.get("definition") or ""
        formula = metric.get("formula") or ""
        pitfalls = metric.get("pitfalls") or ""

        lines = [f"指标：{name}"]
        if short:
            lines.append(f"定义：{short}")
        if formula:
            lines.append(f"公式：{formula}")
        if pitfalls:
            lines.append(f"易错点：{pitfalls}")

        return ToolResult(
            success=True,
            result_for_llm="\n".join(lines),
            metadata={"found": True, "metric": key},
        )
