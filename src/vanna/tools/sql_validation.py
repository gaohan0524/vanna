"""
SQL validation helpers (structured guardrails for Text2SQL).

结构化输出（JSON schema / strict tool calling）只能约束「工具参数的形状」，无法约束
``sql`` 字符串本身是否合法。因此主流 Text2SQL 在生产链路上还会叠加一层**结构化校验**：
执行前解析 SQL，拒绝空语句、多语句（堆叠注入）、超长语句与只读模式下的写操作，并把
结构化错误回喂给 LLM 让其自我修复。

本模块用标准库外的轻量依赖 ``sqlparse``（项目已有）做语句切分与首关键字分类，避免
引入重型解析器；需要 AST 级校验时可替换为 ``sqlglot``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

import sqlparse

# 只读模式允许的首关键字
_READ_ONLY_LEADING_KEYWORDS = {
    "SELECT",
    "WITH",
    "EXPLAIN",
    "SHOW",
    "DESCRIBE",
    "DESC",
    "PRAGMA",
}

# 显式禁止的写操作关键字（防御 WITH ... INSERT 或子查询混入 DML）
_WRITE_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "REPLACE",
    "UPSERT",
    "CALL",
    "EXEC",
    "EXECUTE",
    "VACUUM",
    "ATTACH",
    "DETACH",
    "COMMIT",
    "ROLLBACK",
    "BEGIN",
}


def _strip_sql_comments(sql: str) -> str:
    """移除 SQL 中的块注释与行注释，便于关键字判定。"""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def split_statements(sql: str) -> List[str]:
    """用 sqlparse 把 SQL 切成独立语句（自动处理字符串/注释里的分号）。"""
    return [statement for statement in sqlparse.split(sql) if statement.strip()]


def _ast_read_only(sql: str) -> Optional[bool]:
    """用 sqlglot 做 AST 级只读判断。

    - True：明确的 SELECT/WITH（不会误杀字符串里的 ``'delete'``）；
    - False：明确的 Insert/Update/Delete/Drop 等写操作；
    - None：解析失败或无法判断（SHOW/EXPLAIN 等），由关键字逻辑兜底。
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - 主依赖已包含
        return None

    try:
        statements = [
            statement
            for statement in sqlglot.parse(sql, read="clickhouse")
            if statement is not None
        ]
    except Exception:
        return None

    if len(statements) != 1:
        return None

    node = statements[0]
    if isinstance(node, (exp.Select, exp.Union)):
        return True

    write_nodes = tuple(
        cls
        for name in (
            "Insert",
            "Update",
            "Delete",
            "Drop",
            "Create",
            "Alter",
            "TruncateTable",
            "Merge",
        )
        if (cls := getattr(exp, name, None)) is not None
    )
    if write_nodes and isinstance(node, write_nodes):
        return False
    return None


def _is_read_only_query(sql: str) -> bool:
    """判断 SQL 是否为只读查询（AST 优先，无法判断时回退到关键字）。"""
    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        return False

    leading = cleaned.split(None, 1)[0].upper()
    if leading not in _READ_ONLY_LEADING_KEYWORDS:
        return False

    ast_result = _ast_read_only(sql)
    if ast_result is not None:
        return ast_result

    upper = cleaned.upper()
    return not any(re.search(rf"\b{kw}\b", upper) for kw in _WRITE_KEYWORDS)


@dataclass
class SqlValidationResult:
    """结构化校验结果：ok=False 时 error_type 可用于 LLM 自我修复与审计分类。"""

    ok: bool
    reason: Optional[str] = None
    error_type: Optional[str] = None
    statement_type: Optional[str] = None


def validate_sql(
    sql: str,
    *,
    read_only: bool = False,
    max_length: Optional[int] = None,
    require_single_statement: bool = True,
) -> SqlValidationResult:
    """Validate a SQL string before execution.

    Args:
        sql: SQL text produced by the model.
        read_only: If True, reject write statements.
        max_length: Optional max character length (guard against prompt-injected blobs).
        require_single_statement: If True, reject stacked statements (``;`` separated).

    返回 ``SqlValidationResult``。失败时不抛异常，由调用方把 reason 回喂给 LLM。
    """
    if not sql or not sql.strip():
        return SqlValidationResult(
            ok=False, reason="SQL must not be empty.", error_type="empty_sql"
        )

    if max_length is not None and len(sql) > max_length:
        return SqlValidationResult(
            ok=False,
            reason=(
                f"SQL exceeds the maximum length of {max_length} characters "
                f"({len(sql)} given)."
            ),
            error_type="sql_too_long",
        )

    if require_single_statement:
        statements = split_statements(sql)
        if len(statements) > 1:
            return SqlValidationResult(
                ok=False,
                reason=(
                    "Only a single SQL statement is allowed; "
                    f"{len(statements)} statements were provided. "
                    "Please return exactly one statement."
                ),
                error_type="multiple_statements",
            )

    cleaned = _strip_sql_comments(sql).strip()
    statement_type = cleaned.split(None, 1)[0].upper() if cleaned else None

    if read_only and not _is_read_only_query(sql):
        return SqlValidationResult(
            ok=False,
            reason=(
                "Write operations are not allowed in read-only mode. "
                f"Rejected query: {sql.strip()[:200]}"
            ),
            error_type="read_only_violation",
            statement_type=statement_type,
        )

    return SqlValidationResult(ok=True, statement_type=statement_type)
