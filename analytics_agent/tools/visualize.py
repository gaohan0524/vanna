"""`visualize_data` 的业务侧封装：只改 description，不改实现。"""

from __future__ import annotations

from vanna.tools import VisualizeDataTool


class AnalyticsVisualizeTool(VisualizeDataTool):
    """把上游的中文/英文描述统一成“四段式”，便于模型路由。

    适用：
    - 已有 `run_sql` 结果，用户要看趋势或对比；
    - 不需要再查库，只需要把结果画出来。

    不适用：
    - 还没有结果（先 `run_sql`）；
    - 想用图表工具查数（它不执行 SQL）。
    """

    @property
    def description(self) -> str:
        return (
            "visualize_data：把上一次查询结果生成图表。\n"
            "用：已有 run_sql 结果，用户要看趋势或对比。\n"
            "不要用：没有结果时（先 run_sql）；不要用它查数或改 SQL。\n"
            "分工：run_sql 出结果 → visualize_data 画图。"
        )
