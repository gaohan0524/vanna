"""Report generation: JSON (machine), HTML (human), CSV (triage)."""

from __future__ import annotations

import csv
import html
import json
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _p95(values: Iterable[float]) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    index = max(0, min(len(data) - 1, int(round(0.95 * (len(data) - 1)))))
    return float(data[index])


def build_payload(
    *,
    dataset_name: str,
    dataset_path: str,
    agent_info: Dict[str, Any],
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate per-case records into a report payload."""
    answer = [record for record in records if record["expected_behavior"] == "answer"]
    other = [record for record in records if record["expected_behavior"] != "answer"]

    def _rate(items: List[Dict[str, Any]], key: str) -> float | None:
        # 没有任何该类用例时返回 None，避免把“不适用”显示成 0%
        return (
            round(sum(1 for item in items if item[key]) / len(items), 4)
            if items
            else None
        )

    by_layer: Dict[str, Dict[str, int]] = {}
    for record in records:
        bucket = by_layer.setdefault(record["layer"] or "-", {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(bool(record["passed"]))

    by_taxonomy: Dict[str, int] = {}
    for record in records:
        if record["passed"]:
            continue
        by_taxonomy[record["taxonomy"]] = by_taxonomy.get(record["taxonomy"], 0) + 1

    return {
        "dataset": {"name": dataset_name, "path": dataset_path},
        "agent": agent_info,
        "summary": {
            "cases": len(records),
            "ex": _rate(answer, "passed"),
            "first_try": _rate(answer, "first_try"),
            "behavior_accuracy": _rate(other, "passed"),
            "avg_tool_calls": round(
                statistics.fmean([record["tool_calls"] for record in records]), 2
            )
            if records
            else 0.0,
            "p95_latency_ms": round(_p95([record["latency_ms"] for record in records])),
            "avg_prompt_chars": round(
                statistics.fmean([record["prompt_chars"] for record in records])
            )
            if records
            else 0,
            "est_tokens_per_case": round(
                statistics.fmean([record["prompt_chars"] for record in records]) / 4
            )
            if records
            else 0,
        },
        "by_layer": by_layer,
        "by_taxonomy": by_taxonomy,
        "cases": records,
    }


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _render_html(payload: Dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = []
    for record in payload["cases"]:
        status = "PASS" if record["passed"] else "FAIL"
        css = "pass" if record["passed"] else "fail"
        rows.append(
            "<tr class='{css}'>"
            "<td>{id}</td><td>{layer}</td><td>{behavior}</td><td>{status}</td>"
            "<td>{first}</td><td>{calls}</td><td>{taxonomy}</td>"
            "<td><code>{sql}</code></td><td>{reason}</td></tr>".format(
                css=css,
                id=html.escape(record["id"]),
                layer=html.escape(record["layer"] or "-"),
                behavior=html.escape(record["expected_behavior"]),
                status=status,
                first="Y" if record["first_try"] else "N",
                calls=record["tool_calls"],
                taxonomy=html.escape(record["taxonomy"]),
                sql=html.escape(record.get("executed_sql") or ""),
                reason=html.escape(record.get("reason") or ""),
            )
        )

    taxonomy_rows = "".join(
        f"<tr><td>{html.escape(code)}</td><td>{count}</td></tr>"
        for code, count in sorted(
            payload["by_taxonomy"].items(), key=lambda item: -item[1]
        )
    )
    layer_rows = "".join(
        f"<tr><td>{html.escape(layer)}</td><td>{bucket['passed']}/{bucket['total']}</td></tr>"
        for layer, bucket in sorted(payload["by_layer"].items())
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Stage 0 evaluation report</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; }}
h1 {{ font-size: 20px; }}
.metrics {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }}
.metric {{ border: 1px solid #e4e7eb; border-radius: 8px; padding: 10px 14px; min-width: 150px; }}
.metric b {{ display: block; font-size: 20px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; font-size: 13px; }}
th, td {{ border-bottom: 1px solid #e4e7eb; padding: 6px 8px; text-align: left; vertical-align: top; }}
code {{ font-size: 12px; white-space: pre-wrap; word-break: break-word; }}
tr.fail td {{ background: #fff5f5; }}
tr.pass td {{ background: #f7fdf9; }}
</style></head><body>
<h1>Stage 0 evaluation report — {html.escape(payload["dataset"]["name"])}</h1>
<div class="metrics">
  <div class="metric">EX<b>{_pct(summary["ex"])}</b></div>
  <div class="metric">First-try<b>{_pct(summary["first_try"])}</b></div>
  <div class="metric">Behavior<b>{_pct(summary["behavior_accuracy"])}</b></div>
  <div class="metric">Avg tool calls<b>{summary["avg_tool_calls"]}</b></div>
  <div class="metric">P95 latency<b>{summary["p95_latency_ms"]} ms</b></div>
  <div class="metric">Est. tokens/case<b>{summary["est_tokens_per_case"]}</b></div>
</div>
<h2>By layer</h2><table><tr><th>Layer</th><th>Passed</th></tr>{layer_rows}</table>
<h2>Failures by taxonomy</h2><table><tr><th>Code</th><th>Count</th></tr>{taxonomy_rows}</table>
<h2>Cases</h2>
<table><tr><th>ID</th><th>Layer</th><th>Expected</th><th>Result</th><th>First</th>
<th>Calls</th><th>Taxonomy</th><th>Executed SQL</th><th>Reason</th></tr>
{"".join(rows)}
</table></body></html>
"""


def _write_csv(path: Path, records: List[Dict[str, Any]]) -> None:
    fields = [
        "id",
        "layer",
        "expected_behavior",
        "passed",
        "first_try",
        "taxonomy",
        "tool_calls",
        "latency_ms",
        "executed_sql",
        "answer",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def write_reports(out_dir: Path | str, payload: Dict[str, Any]) -> Dict[str, Path]:
    """Write report.json / report.html / failures.csv and return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "report.json"
    html_path = out_dir / "report.html"
    csv_path = out_dir / "failures.csv"

    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    html_path.write_text(_render_html(payload), encoding="utf-8")
    _write_csv(
        csv_path, [record for record in payload["cases"] if not record["passed"]]
    )

    return {"json": json_path, "html": html_path, "csv": csv_path}
