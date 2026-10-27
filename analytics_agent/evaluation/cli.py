"""Stage 0 evaluation CLI.

python -m analytics_agent.evaluation.cli check
python -m analytics_agent.evaluation.cli seed --scale 0.1
python -m analytics_agent.evaluation.cli preview --case A-01,C-15
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/smoke.yaml
python -m analytics_agent.evaluation.cli report --run reports/20261022-101500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from vanna.core.evaluation import EvaluationDataset, EvaluationRunner

from analytics_agent.agent_factory import build_agent
from analytics_agent.config import Settings

from .evaluators import (
    ClarificationEvaluator,
    FirstTryEvaluator,
    ResultSetEvaluator,
    behavior_of,
    extract_sqls,
    golden_sql_of,
)
from .compare import results_equal
from .executor import execute_safe
from .labeling import classify
from .preflight import run_checks
from .report import build_payload, write_reports


def _default_dataset(settings: Settings) -> str:
    return str(settings.datasets_dir / "v1_30.yaml")


def _prompt_chars(agent_result) -> int:
    total = 0
    for request in agent_result.llm_requests:
        total += len(request.get("system_prompt") or "")
        for message in request.get("messages", []):
            total += len(message.get("content") or "")
    return total


async def _cmd_check(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    dataset = args.dataset or _default_dataset(settings)
    results = await run_checks(
        dataset,
        settings,
        skip_golden=args.skip_golden,
        strict_counts=args.strict,
    )

    width = max(len(result.name) for result in results)
    failures = 0
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        failures += 0 if result.ok else 1
        print(f"[{mark}] {result.name:<{width}}  {result.detail}")

    print(f"\n{len(results) - failures}/{len(results)} checks passed")
    return 0 if failures == 0 else 1


async def _cmd_preview(args: argparse.Namespace) -> int:
    """Print question + golden SQL result for manual review (D2-2)."""
    settings = Settings.from_env()
    dataset_path = args.dataset or _default_dataset(settings)
    dataset = EvaluationDataset.from_yaml(dataset_path)

    wanted = None
    if args.case:
        wanted = {item.strip().upper() for item in args.case.split(",") if item.strip()}

    cases = [
        case
        for case in dataset.test_cases
        if wanted is None or case.id.upper() in wanted
    ]
    if wanted is not None:
        missing = wanted - {case.id.upper() for case in cases}
        if missing:
            print(f"unknown case id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    rows = max(1, args.rows)
    for case in cases:
        meta = case.metadata or {}
        print("=" * 78)
        print(
            f"{case.id}  layer={meta.get('layer', '-')}  expected={behavior_of(case)}"
        )
        print(f"Q: {case.message}")

        sql = golden_sql_of(case) if behavior_of(case) == "answer" else None
        if sql is None:
            print("(no golden_sql: should clarify or refuse)")
            continue

        started = time.perf_counter()
        frame, error = await execute_safe(sql)
        elapsed = time.perf_counter() - started
        if frame is None:
            print(f"ERROR: {error}")
            continue

        print(f"rows={len(frame)} cols={frame.shape[1]} time={elapsed:.2f}s")
        print(frame.head(rows).to_string(index=False))

    print("=" * 78)
    print(f"{len(cases)} case(s) previewed")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    from .seed.generate import generate

    settings = Settings.from_env()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    # 只重建表结构时必须 drop，否则 CREATE IF NOT EXISTS 不会更新已有表的 COMMENT
    drop = args.drop or args.schema_only
    counts = generate(
        settings,
        as_of=as_of,
        seed=args.seed,
        scale=args.scale,
        drop=drop,
        schema_only=args.schema_only,
    )
    if args.schema_only:
        print("schema rebuilt (no rows inserted, anchor not written)")
        return 0
    for table, rows in counts.items():
        print(f"{table:20s} {rows:>10,d}")
    return 0


def _preview(df, max_rows: int = 5, max_width: int = 240) -> str:
    """Compact DataFrame preview for console diagnostics."""
    if df is None:
        return "<none>"
    body = df.head(max_rows).to_string(index=False)
    lines = [line[:max_width] for line in body.splitlines()]
    return f"{len(df)} rows x {len(df.columns)} cols\n" + "\n".join(lines)


def _tool_line(tool_call) -> str:
    if isinstance(tool_call, dict):
        name = tool_call.get("tool_name") or tool_call.get("name") or "?"
        arguments = tool_call.get("arguments", {})
    else:
        name = getattr(tool_call, "name", "?")
        arguments = getattr(tool_call, "arguments", {})
    try:
        args = json.dumps(arguments, ensure_ascii=False, default=str)
    except Exception:
        args = str(arguments)
    return f"{name}({args[:240]})"


async def _print_case_progress(
    result,
    completed: int,
    total: int,
    taxonomy: str,
) -> None:
    """Print one completed case immediately, including SQL and result diff."""
    case = result.test_case
    agent_result = result.agent_result
    passed = result.overall_passed()
    evaluations = {item.evaluator_name: item for item in result.evaluations}
    result_set = evaluations.get("result_set")
    failing = next((item for item in result.evaluations if not item.passed), None)
    sqls = extract_sqls(agent_result)

    status = "PASS" if passed else f"FAIL[{taxonomy}]"
    print(
        f"\n[{completed:02d}/{total:02d}] {case.id} {status} "
        f"{round(result.execution_time_ms)}ms tools={len(agent_result.tool_calls)}",
        flush=True,
    )

    for tool_call in agent_result.tool_calls[:8]:
        print(f"  TOOL {_tool_line(tool_call)}", flush=True)

    if sqls:
        for index, sql in enumerate(sqls, start=1):
            print(f"  SQL[{index}]:\n{_indent(sql.strip(), 4)}", flush=True)
    else:
        print("  SQL: <none>", flush=True)

    display_sql = None
    if result_set and result_set.metrics.get("executed_sql"):
        display_sql = str(result_set.metrics["executed_sql"])
    elif sqls:
        display_sql = sqls[-1]

    actual = golden = None
    if display_sql:
        actual, actual_error = await execute_safe(display_sql)
        if actual is None:
            print(f"  ACTUAL: ERROR {actual_error}", flush=True)
        else:
            print(f"  ACTUAL:\n{_indent(_preview(actual), 4)}", flush=True)

    golden_sql = golden_sql_of(case)
    if golden_sql:
        golden, golden_error = await execute_safe(golden_sql)
        if golden is None:
            print(f"  GOLDEN: ERROR {golden_error}", flush=True)
        else:
            print(f"  GOLDEN:\n{_indent(_preview(golden), 4)}", flush=True)

    if actual is not None and golden is not None:
        metadata = case.metadata or {}
        comparison = results_equal(
            golden,
            actual,
            order_sensitive=bool(metadata.get("order_sensitive", False)),
            column_alias=metadata.get("column_alias") or None,
            column_match=metadata.get("column_match", "projection"),
        )
        if comparison.equal:
            print("  COMPARE: MATCH", flush=True)
        else:
            print(
                f"  COMPARE: MISMATCH {comparison.reason}: {comparison.detail}",
                flush=True,
            )

    if failing and not passed:
        print(f"  REASON: {failing.reasoning[:600]}", flush=True)

    answer = agent_result.get_final_answer().strip()
    if answer:
        print(f"  ANSWER: {answer[:400]}", flush=True)


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line for line in str(text).splitlines())


async def _cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    dataset_path = args.dataset or _default_dataset(settings)
    dataset = EvaluationDataset.from_yaml(dataset_path)
    cases = dataset.test_cases
    if args.limit:
        cases = cases[: args.limit]

    agent = build_agent(settings)
    runner = EvaluationRunner(
        evaluators=[
            ResultSetEvaluator(),
            ClarificationEvaluator(),
            FirstTryEvaluator(),
        ],
        max_concurrency=args.concurrency,
    )

    print(
        f"Running {len(cases)} cases against {settings.llm_provider}/{settings.llm_model} ...",
        flush=True,
    )

    results = []
    taxonomies: Dict[str, str] = {}
    async for test_case_result, completed, total in runner.run_evaluation_streaming(
        agent, cases
    ):
        case = test_case_result.test_case
        passed = test_case_result.overall_passed()
        taxonomy = (
            "OK" if passed else await classify(case, test_case_result.agent_result)
        )
        taxonomies[case.id] = taxonomy
        await _print_case_progress(test_case_result, completed, total, taxonomy)
        results.append(test_case_result)

    records: List[Dict[str, Any]] = []
    for test_case_result in results:
        case = test_case_result.test_case
        agent_result = test_case_result.agent_result
        evaluations = {
            item.evaluator_name: item for item in test_case_result.evaluations
        }
        result_set = evaluations.get("result_set")
        first_try = evaluations.get("first_try")
        passed = test_case_result.overall_passed()

        taxonomy = taxonomies[case.id]
        sqls = extract_sqls(agent_result)
        executed_sql = ""
        if result_set and result_set.metrics.get("executed_sql"):
            executed_sql = str(result_set.metrics["executed_sql"])
        elif sqls:
            executed_sql = sqls[-1]

        failing = next(
            (item for item in test_case_result.evaluations if not item.passed), None
        )
        skipped_first_try = bool(first_try and first_try.metrics.get("skipped"))

        records.append(
            {
                "id": case.id,
                "layer": str((case.metadata or {}).get("layer", "")),
                "expected_behavior": behavior_of(case),
                "passed": passed,
                "first_try": bool(
                    first_try and first_try.passed and not skipped_first_try
                ),
                "taxonomy": taxonomy,
                "tool_calls": len(agent_result.tool_calls),
                "queries": len(sqls),
                "latency_ms": round(test_case_result.execution_time_ms),
                "prompt_chars": _prompt_chars(agent_result),
                "executed_sql": executed_sql,
                "answer": agent_result.get_final_answer()[:500],
                "reason": failing.reasoning if failing else "",
                "error": agent_result.error or "",
            }
        )

    order = {case.id: index for index, case in enumerate(cases)}
    records.sort(key=lambda record: order.get(record["id"], 10**9))

    print("\nSummary (dataset order):", flush=True)
    print(f"{'case':8} {'result':20} {'ms':>7} {'tools':>5}  reason", flush=True)
    for record in records:
        status = "PASS" if record["passed"] else f"FAIL[{record['taxonomy']}]"
        reason = " ".join((record["reason"] or "").split())[:90]
        print(
            f"{record['id']:8} {status:20} {record['latency_ms']:>7} "
            f"{record['tool_calls']:>5}  {reason}",
            flush=True,
        )

    tool_names = await agent.tool_registry.list_tools()
    payload = build_payload(
        dataset_name=dataset.name,
        dataset_path=dataset_path,
        agent_info={
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "temperature": 0.0,
            "max_tool_iterations": settings.agent_max_iterations,
            "prompt_version": getattr(
                agent.system_prompt_builder, "prompt_version", "unknown"
            ),
            "prompt_tokens_est": getattr(
                agent.system_prompt_builder, "last_prompt_tokens", 0
            ),
            # 全部已注册工具（不区分用户分组），用于复盘“这次有哪些工具可用”
            "tool_names": tool_names,
        },
        records=records,
    )

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else settings.reports_dir / run_id

    paths = write_reports(out_dir, payload)
    summary = payload["summary"]

    def _pct(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value:.1%}"

    print(
        f"\nEX={_pct(summary['ex'])}  first_try={_pct(summary['first_try'])}  "
        f"behavior={_pct(summary['behavior_accuracy'])}  "
        f"avg_tools={summary['avg_tool_calls']}  p95={summary['p95_latency_ms']}ms"
    )
    print(f"report: {paths['html']}")
    print(f"json:   {paths['json']}")
    print(f"fails:  {paths['csv']}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    json_path = run_dir / "report.json"
    if not json_path.exists():
        print(f"report.json not found under {run_dir}", file=sys.stderr)
        return 1
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    paths = write_reports(run_dir, payload)
    print(f"regenerated: {paths['html']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m analytics_agent.evaluation.cli",
        description="Stage 0 evaluation harness for the ClickHouse analytics agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="preflight + golden SQL self-check")
    check.add_argument("--dataset", help="path to the dataset yaml")
    check.add_argument("--skip-golden", action="store_true")
    check.add_argument("--strict", action="store_true", help="require >= 30 cases")
    check.set_defaults(func=lambda args: _cmd_check(args))

    preview = sub.add_parser(
        "preview", help="print question + golden result for manual review"
    )
    preview.add_argument("--dataset", help="path to the dataset yaml")
    preview.add_argument("--case", help="comma separated ids, e.g. A-01,C-15")
    preview.add_argument(
        "--rows", type=int, default=5, help="rows to print (default 5)"
    )
    preview.set_defaults(func=lambda args: _run_sync(_cmd_preview, args))

    seed = sub.add_parser("seed", help="(re)generate the synthetic warehouse")
    seed.add_argument("--as-of", help="anchor date YYYY-MM-DD (default: yesterday)")
    seed.add_argument("--seed", type=int, default=42)
    seed.add_argument("--scale", type=float, default=1.0)
    seed.add_argument("--drop", action="store_true")
    seed.add_argument(
        "--schema-only",
        action="store_true",
        help="drop & recreate tables with comments, insert no rows",
    )
    seed.set_defaults(func=lambda args: _cmd_seed(args))

    run = sub.add_parser("run", help="run the agent on a dataset")
    run.add_argument("--dataset", help="path to the dataset yaml")
    run.add_argument("--out", help="output directory (default: reports/<timestamp>)")
    run.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="keep 1 in Stage 0 so trajectories stay attributable",
    )
    run.add_argument("--limit", type=int, default=0, help="only run the first N cases")
    run.set_defaults(func=lambda args: _run_sync(_cmd_run, args))

    report = sub.add_parser("report", help="re-render html/csv from report.json")
    report.add_argument("--run", required=True, help="directory containing report.json")
    report.set_defaults(func=lambda args: _cmd_report(args))

    return parser


def _run_sync(func, args: argparse.Namespace) -> int:
    return asyncio.run(func(args))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return asyncio.run(_cmd_check(args))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
