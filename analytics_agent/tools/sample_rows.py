"""`sample_rows` 工具：查看一张表的前 N 行真实数据。

定位：
- 需要连数据库（与 `search_columns` / `describe_table` 不同）；
- 回答“枚举大小写、日期格式、脏数据长什么样”这类只看 catalog 看不出来的问题；
- 强制表名白名单 + `LIMIT` + 10s 超时 + 脱敏，避免把全表或 PII 回喂给模型。

典型链路：
    describe_table 看完字段 → sample_rows 确认取值 → run_sql 正式查询
"""

from __future__ import annotations

import re
from typing import Any, Dict, Type

import pandas as pd
from pydantic import Field
from vanna import Tool, ToolContext, ToolResult
from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.tools.sql_errors import classify_sql_error, error_payload

from analytics_agent.context.search import find_table, suggest_tables
from analytics_agent.tools.params import TABLE_PATTERN, ToolArgs

# 只用于“看起来像邮箱/手机号”的字符串；不处理纯数字列，避免误伤 ID。
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

MAX_STRING_LEN = 80
MAX_RESULT_CHARS = 4_000


class SampleRowsArgs(ToolArgs):
    table: str = Field(
        min_length=1,
        max_length=60,
        pattern=TABLE_PATTERN,
        description="表名，可带库名，如 orders 或 shop.orders",
    )
    n: int = Field(default=5, ge=1, le=20, description="返回行数（1~20）")


def mask_value(value: Any, max_len: int = MAX_STRING_LEN) -> Any:
    """对单个值做脱敏 + 字符串截断。

    - 邮箱 -> ``[email]``
    - 中国大陆手机号 -> ``[phone]``
    - 其它字符串超过 ``max_len`` 时截断并加省略号
    """
    if not isinstance(value, str):
        return value

    text = _EMAIL_RE.sub("[email]", value)
    text = _PHONE_RE.sub("[phone]", text)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def mask_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """逐列脱敏，返回新 DataFrame（不修改调用方数据）。"""
    masked = frame.copy()
    for column in masked.columns:
        masked[column] = masked[column].map(mask_value)
    return masked


class SampleRowsTool(Tool[SampleRowsArgs]):
    """查看单表前 N 行真实数据（已脱敏）。

    适用：
    - 确认枚举大小写（``PAID`` vs ``paid``）、日期格式、脏数据；
    - describe_table 只看注释不够，需要看真实取值。

    不适用：
    - 聚合/统计 → run_sql；
    - 不知道表名 → search_columns / describe_table。

    安全：表名必须在 catalog 中；强制 ``LIMIT n``；10s 服务端超时；邮箱/手机号脱敏。
    """

    def __init__(self, catalog: Dict[str, Any], runner, timeout_seconds: float = 10.0):
        self._catalog = catalog
        self._runner = runner
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "sample_rows"

    @property
    def description(self) -> str:
        return (
            "sample_rows：查看单表前 N 行真实数据（已脱敏）。\n"
            "用：确认枚举大小写、日期格式、JSON 键名、脏数据。\n"
            "不要用：聚合统计（用 run_sql）、未知表名（先 search_columns / describe_table）。\n"
            "分工：describe_table 看字段 → sample_rows 看真实值 → run_sql 取数。"
        )

    def get_args_schema(self) -> Type[SampleRowsArgs]:
        return SampleRowsArgs

    async def _execute(self, sql: str, context: ToolContext) -> pd.DataFrame:
        settings = {"max_execution_time": self._timeout}
        run_query = getattr(self._runner, "run_query", None)
        if callable(run_query):
            return await run_query(sql, settings=settings)
        # 兼容只实现 SqlRunner 接口的 runner
        return await self._runner.run_sql(RunSqlToolArgs(sql=sql), context)

    async def execute(self, context: ToolContext, args: SampleRowsArgs) -> ToolResult:
        table = find_table(self._catalog, args.table)
        if table is None:
            suggestions = suggest_tables(self._catalog, args.table)
            hint = (
                "相近的表：" + "、".join(suggestions)
                if suggestions
                else "请先用 search_columns 找到候选表。"
            )
            return ToolResult(
                success=False,
                result_for_llm=f"未找到表 '{args.table}'。{hint}",
                error="unknown_table",
            )

        database = self._catalog.get("database", "shop")
        name = table.get("name", "")
        sql = f"SELECT * FROM {database}.`{name}` LIMIT {args.n}"

        try:
            frame = await self._execute(sql, context)
        except Exception as exc:  # 数据库错误要回喂给模型，而不是抛给上层
            info = classify_sql_error(exc)
            message = f"查询样例失败：{type(exc).__name__}: {exc}"
            payload = error_payload(info.error_type, message, info.hint)
            return ToolResult(
                success=False,
                result_for_llm=payload,
                error="sample_failed",
                metadata={"error_type": info.error_type, "hint": info.hint},
            )

        masked = mask_frame(frame)
        text = masked.to_string(index=False)
        truncated = len(text) > MAX_RESULT_CHARS
        if truncated:
            text = text[:MAX_RESULT_CHARS] + "\n…（结果已截断）"

        header = (
            f"表：{database}.{name}（前 {len(frame)} 行，"
            f"共 {len(frame.columns)} 列；已脱敏）"
        )
        return ToolResult(
            success=True,
            result_for_llm=f"{header}\n{text}",
            metadata={
                "table": name,
                "rows": len(frame),
                "columns": len(frame.columns),
                "truncated": truncated,
            },
        )
