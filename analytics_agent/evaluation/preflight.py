"""Preflight checks: fail fast before spending money on LLM calls.

检查项（与 `docs/STAGE0-EVAL.md` 7.1 一致）：
1. ClickHouse 可连接
2. 查询账号确实只读（CREATE TABLE 必须失败）
3. 合成库表齐全且有数据
4. 数据锚点 == 昨天（防止时间类用例失真）
5. 用例 id 唯一、行为合法、golden SQL 可执行且非空
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from vanna.core.evaluation import EvaluationDataset

from analytics_agent.config import Settings

from .evaluators import behavior_of, golden_sql_of
from .executor import execute_readonly, get_client

REQUIRED_TABLES = {
    "dim_date": 100,
    "users": 1_000,
    "products": 100,
    "orders": 1_000,
    "order_items": 1_000,
    "payments": 1_000,
    "refunds": 100,
    "sessions": 1_000,
    "events": 1_000,
    "campaigns": 100,
    "accounts": 100,
    "meta_data_anchor": 1,
}

VALID_BEHAVIORS = {"answer", "clarify", "refuse"}
MAX_GOLDEN_SECONDS = 5.0


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


async def _check_connectivity(settings: Settings) -> CheckResult:
    try:
        await execute_readonly("SELECT 1", settings=settings)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("clickhouse.connect", False, f"{type(exc).__name__}: {exc}")
    return CheckResult(
        "clickhouse.connect", True, f"{settings.ch_host}:{settings.ch_port}"
    )


def _check_readonly(settings: Settings) -> CheckResult:
    client = get_client(settings)
    probe = f"{settings.ch_database}.__readonly_probe"
    try:
        client.command(f"CREATE TABLE {probe} (x UInt8) ENGINE = Memory")
    except Exception:
        return CheckResult("clickhouse.readonly", True, "write denied as expected")
    client.command(f"DROP TABLE IF EXISTS {probe}")
    return CheckResult(
        "clickhouse.readonly",
        False,
        "the query account can CREATE TABLE; use a read-only account",
    )


async def _check_tables(settings: Settings) -> List[CheckResult]:
    results: List[CheckResult] = []
    database = settings.ch_database.replace("'", "\\'")
    try:
        tables = await execute_readonly(
            f"SELECT name FROM system.tables WHERE database = '{database}'",
            settings=settings,
        )
        present = {str(value) for value in tables.iloc[:, 0].tolist()}
    except Exception as exc:  # noqa: BLE001
        return [CheckResult("tables", False, f"{type(exc).__name__}: {exc}")]

    missing = sorted(set(REQUIRED_TABLES) - present)
    results.append(
        CheckResult(
            "tables.exist",
            not missing,
            f"missing={missing}"
            if missing
            else f"{len(REQUIRED_TABLES)} tables present",
        )
    )

    for table, minimum in REQUIRED_TABLES.items():
        if table in missing:
            continue
        try:
            frame = await execute_readonly(
                f"SELECT count() FROM {settings.ch_database}.{table}", settings=settings
            )
            rows = int(frame.iloc[0, 0])
            results.append(
                CheckResult(
                    f"tables.rows.{table}",
                    rows >= minimum,
                    f"{rows:,} rows (min {minimum:,})",
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                CheckResult(
                    f"tables.rows.{table}", False, f"{type(exc).__name__}: {exc}"
                )
            )
    return results


async def _check_anchor(settings: Settings) -> CheckResult:
    try:
        frame = await execute_readonly(
            f"SELECT max(anchor_date) FROM {settings.ch_database}.meta_data_anchor",
            settings=settings,
        )
        anchor = frame.iloc[0, 0]
    except Exception as exc:  # noqa: BLE001
        return CheckResult("data.anchor", False, f"{type(exc).__name__}: {exc}")

    expected = date.today() - timedelta(days=1)
    actual = anchor if isinstance(anchor, date) else date.fromisoformat(str(anchor))
    return CheckResult(
        "data.anchor",
        actual == expected,
        f"anchor={actual} expected={expected} (re-run seed if stale)",
    )


def _check_dataset(dataset: EvaluationDataset) -> List[CheckResult]:
    results: List[CheckResult] = []
    ids = [case.id for case in dataset.test_cases]
    duplicated = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    results.append(
        CheckResult(
            "dataset.ids",
            not duplicated,
            f"duplicated={duplicated}" if duplicated else f"{len(ids)} cases",
        )
    )

    bad_behavior = [
        case.id
        for case in dataset.test_cases
        if behavior_of(case) not in VALID_BEHAVIORS
    ]
    results.append(
        CheckResult(
            "dataset.behavior",
            not bad_behavior,
            f"invalid={bad_behavior}" if bad_behavior else "all valid",
        )
    )

    missing_golden = [
        case.id
        for case in dataset.test_cases
        if behavior_of(case) == "answer" and golden_sql_of(case) is None
    ]
    results.append(
        CheckResult(
            "dataset.golden",
            not missing_golden,
            f"missing golden_sql={missing_golden}"
            if missing_golden
            else "all answer cases have golden_sql",
        )
    )
    return results


async def _check_golden_sql(dataset: EvaluationDataset) -> List[CheckResult]:
    results: List[CheckResult] = []
    for case in dataset.test_cases:
        sql = golden_sql_of(case) if behavior_of(case) == "answer" else None
        if sql is None:
            continue
        started = time.perf_counter()
        try:
            frame = await execute_readonly(sql)
        except Exception as exc:  # noqa: BLE001
            results.append(
                CheckResult(f"golden.{case.id}", False, f"{type(exc).__name__}: {exc}")
            )
            continue
        elapsed = time.perf_counter() - started
        if frame.empty:
            results.append(CheckResult(f"golden.{case.id}", False, "returned 0 rows"))
        elif elapsed > MAX_GOLDEN_SECONDS:
            results.append(
                CheckResult(
                    f"golden.{case.id}",
                    False,
                    f"took {elapsed:.2f}s (>{MAX_GOLDEN_SECONDS}s)",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"golden.{case.id}",
                    True,
                    f"{len(frame)} rows, {frame.shape[1]} cols, {elapsed:.2f}s",
                )
            )
    return results


async def run_checks(
    dataset_path: str,
    settings: Optional[Settings] = None,
    *,
    skip_golden: bool = False,
    strict_counts: bool = False,
    expected_cases: int = 30,
) -> List[CheckResult]:
    """Run all preflight checks and return their results."""
    s = settings or Settings.from_env()
    dataset = EvaluationDataset.from_yaml(dataset_path)

    results: List[CheckResult] = [await _check_connectivity(s)]
    if results[-1].ok:
        results.append(_check_readonly(s))
        results.extend(await _check_tables(s))
        results.append(await _check_anchor(s))

    results.extend(_check_dataset(dataset))

    if strict_counts:
        results.append(
            CheckResult(
                "dataset.count",
                len(dataset.test_cases) >= expected_cases,
                f"{len(dataset.test_cases)} cases (expected >= {expected_cases})",
            )
        )

    if not skip_golden:
        results.extend(await _check_golden_sql(dataset))

    return results
