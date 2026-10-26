"""结构化 SQL 错误：分类 + 可执行的修正建议。

为什么需要：
- 把原始异常直接回喂，模型只能看到一长串堆栈，容易原样重试；
- 分类后给出 ``error_type`` + ``hint``，模型下一轮能按提示改 SQL。

注意：这里只做“分类 + 文案”，不吞异常、不做重试。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SqlErrorInfo:
    error_type: str
    hint: str


# ---------------------------------------------------------------------------
# 校验类错误（validate_sql / guardrails 产生）
# ---------------------------------------------------------------------------

_VALIDATION_HINTS = {
    "empty_sql": "SQL 不能为空，请给出完整查询。",
    "sql_too_long": "SQL 过长，去掉重复的 CTE/子查询后再试。",
    "multiple_statements": "只保留一条 SELECT/WITH，不要用分号堆叠多条语句。",
    "read_only_violation": "只允许 SELECT/WITH；删除 INSERT/UPDATE/DELETE/DROP 后重试。",
    "unsupported_format": "去掉 FORMAT 子句；结果由工具统一序列化。",
    "parse_error": "SQL 无法解析，检查括号、引号与 ClickHouse 方言；不要原样重试。",
    "invalid_sql": "按错误信息修改后重试一次。",
}


def hint_for_error(error_type: str) -> str:
    return _VALIDATION_HINTS.get(error_type, "按错误信息修改后重试一次，不要原样重试。")


# ---------------------------------------------------------------------------
# 执行类错误（ClickHouse 异常）
# ---------------------------------------------------------------------------

_EXECUTION_RULES: tuple[tuple[tuple[str, ...], SqlErrorInfo], ...] = (
    (
        ("timeout", "timed out", "max_execution_time", "timeout_exceeded", "code: 159"),
        SqlErrorInfo(
            "timeout",
            "查询超时。缩小时间范围、加分区过滤，或先 explain_query 查看扫描量。",
        ),
    ),
    (
        (
            "unknown identifier",
            "unknown expression identifier",
            "missing columns",
            "unknown column",
            "code: 47",
            "code: 207",
        ),
        SqlErrorInfo(
            "unknown_identifier",
            "字段或别名不存在。先用 search_columns 确认字段名，再改 SQL；不要原样重试。",
        ),
    ),
    (
        (
            "unknown table",
            "doesn't exist",
            "unknown database",
            "code: 60",
            "code: 81",
        ),
        SqlErrorInfo(
            "unknown_table",
            "表不存在。先用 search_columns / describe_table 确认表名。",
        ),
    ),
    (
        ("syntax error", "failed at position", "expected", "code: 62"),
        SqlErrorInfo(
            "syntax_error",
            "ClickHouse 语法错误。检查函数名、括号与方言（不支持 DATE_TRUNC/EXTRACT）；只改一次。",
        ),
    ),
    (
        ("memory limit", "memory_limit_exceeded", "code: 241"),
        SqlErrorInfo(
            "memory_limit",
            "内存超限。减少扫描列/行，先聚合或加时间过滤。",
        ),
    ),
    (
        ("division by zero", "code: 153"),
        SqlErrorInfo("division_by_zero", "除零。用 nullIf(x, 0) 包住分母。"),
    ),
    (
        ("readonly", "read-only", "code: 164"),
        SqlErrorInfo("read_only_violation", "当前连接是只读账号，只允许 SELECT/WITH。"),
    ),
    (
        ("connection refused", "network", "socket", "connection reset"),
        SqlErrorInfo(
            "connection_error",
            "数据库连接失败；这是环境问题，不要重复重试同一 SQL。",
        ),
    ),
)


def classify_sql_error(error: BaseException | str) -> SqlErrorInfo:
    text = str(error).lower()
    for patterns, info in _EXECUTION_RULES:
        if any(pattern in text for pattern in patterns):
            return info
    return SqlErrorInfo(
        "sql_error",
        "执行失败。读错误信息，只改一次，不要原样重试。",
    )


# ---------------------------------------------------------------------------
# 统一输出
# ---------------------------------------------------------------------------


def error_payload(error_type: str, message: str, hint: str) -> str:
    """生成回喂给 LLM 的 JSON；不包含堆栈。"""
    return json.dumps(
        {
            "error_type": error_type,
            "message": message,
            "hint": hint,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
