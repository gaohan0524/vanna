"""Stage 0 evaluation CLI.

python -m analytics_agent.evaluation.cli check
python -m analytics_agent.evaluation.cli seed --scale 0.1
python -m analytics_agent.evaluation.cli run --dataset analytics_agent/evaluation/datasets/smoke.yaml
python -m analytics_agent.evaluation.cli report --run reports/20261022-101500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
)
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


def _cmd_seed(args: argparse.Namespace) -> int:
    from .seed.generate import generate

    settings = Settings.from_env()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    counts = generate(
        settings,
        as_of=as_of,
        seed=args.seed,
        scale=args.scale,
        drop=args.drop,
    )
    for table, rows in counts.items():
        print(f"{table:20s} {rows:>10,d}")
    return 0


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
        f"Running {len(cases)} cases against {settings.llm_provider}/{settings.llm_model} ..."
    )
    evaluation = await runner.run_evaluation(agent, cases)

    records: List[Dict[str, Any]] = []
    for test_case_result in evaluation.results:
        case = test_case_result.test_case
        agent_result = test_case_result.agent_result
        evaluations = {
            item.evaluator_name: item for item in test_case_result.evaluations
        }
        result_set = evaluations.get("result_set")
        first_try = evaluations.get("first_try")
        passed = test_case_result.overall_passed()

        taxonomy = "OK" if passed else await classify(case, agent_result)
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

    payload = build_payload(
        dataset_name=dataset.name,
        dataset_path=dataset_path,
        agent_info={
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "temperature": 0.0,
            "max_tool_iterations": 8,
        },
        records=records,
    )

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else settings.reports_dir / run_id

    for record in records:
        mark = "PASS" if record["passed"] else f"FAIL[{record['taxonomy']}]"
        print(f"{record['id']:<12} {mark:<24} {record['latency_ms']:>6} ms")

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

    seed = sub.add_parser("seed", help="(re)generate the synthetic warehouse")
    seed.add_argument("--as-of", help="anchor date YYYY-MM-DD (default: yesterday)")
    seed.add_argument("--seed", type=int, default=42)
    seed.add_argument("--scale", type=float, default=1.0)
    seed.add_argument("--drop", action="store_true")
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
