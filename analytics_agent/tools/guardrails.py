"""SQL 护栏：AST 只读校验 + LIMIT 注入。

为什么不用关键字黑名单：
- `SELECT 'delete' AS s` 里的字符串不是写操作，黑名单会误杀；
- `WITH x AS (...) INSERT INTO ...` 首关键字是 WITH，黑名单可能放过。
AST 解析能按节点类型判断。

为什么不用 `asyncio.wait_for` 做超时：
- `ClickHouseRunner.BLOCKING = True`，线程里的同步查询打不断；
- 超时由 `ClickHouseRunner.query_settings={"max_execution_time": 30}` 交给服务端。
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

# 含 FORMAT 的 SQL 无法安全包一层 LIMIT 子查询；直接拒绝。
_FORMAT_RE = re.compile(r"\bFORMAT\b", re.IGNORECASE)

# run_sql 只允许查询；EXPLAIN 由 explain_query 工具负责。
_ALLOWED_NODES = (exp.Select, exp.Union)


class SqlGuardError(ValueError):
    """护栏拒绝执行时抛出，带结构化 error_type。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _has_top_level_limit(node: exp.Expression) -> bool:
    return node.args.get("limit") is not None


def prepare_sql(sql: str, *, limit: int = 1000) -> tuple[str, str]:
    """校验 SQL 并在无 LIMIT 时包装一层。

    Returns:
        ``(sql_to_execute, note)``；note 为空表示未改写。

    Raises:
        SqlGuardError: 解析失败、多语句、非 SELECT/WITH、含 FORMAT。
    """
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise SqlGuardError("empty_sql", "SQL 不能为空。")

    if _FORMAT_RE.search(text):
        raise SqlGuardError(
            "unsupported_format",
            "含 FORMAT 的 SQL 无法安全限制行数，请去掉 FORMAT 后重试。",
        )

    try:
        statements = [
            statement
            for statement in sqlglot.parse(text, read="clickhouse")
            if statement is not None
        ]
    except Exception as exc:
        raise SqlGuardError("parse_error", f"SQL 解析失败：{exc}") from exc

    if len(statements) != 1:
        raise SqlGuardError(
            "multiple_statements",
            f"只允许单条语句，收到 {len(statements)} 条。",
        )

    node = statements[0]
    if not isinstance(node, _ALLOWED_NODES):
        raise SqlGuardError(
            "read_only_violation",
            f"只允许 SELECT/WITH，收到 {type(node).__name__}。",
        )

    if _has_top_level_limit(node):
        return text, ""

    wrapped = f"SELECT * FROM (\n{text}\n) AS _guarded LIMIT {limit}"
    note = f"已自动追加 LIMIT {limit}（原 SQL 无 LIMIT）。"
    return wrapped, note
