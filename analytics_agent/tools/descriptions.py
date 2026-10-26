"""工具描述常量：需要覆盖上游默认描述时使用。

所有描述统一“四段式”：做什么 / 用 / 不要用 / 分工。
"""

from __future__ import annotations

RUN_SQL_DESCRIPTION = (
    "run_sql：执行一条只读 SQL 并返回结果。\n"
    "用：表、字段、口径都已确定，需要拿结果或验证假设。\n"
    "不要用：猜字段（用 search_columns）、确认枚举/格式（用 sample_rows）、"
    "查指标定义（用 get_metric_definition）、查执行计划（用 explain_query）。\n"
    "分工：先 search_columns / describe_table 定位，再 run_sql 取数；失败只改一次。"
)
