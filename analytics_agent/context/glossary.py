"""Load and render L2 metrics/terms (glossary.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from analytics_agent.config import PROJECT_ROOT

from .render import estimate_tokens

DEFAULT_GLOSSARY = PROJECT_ROOT / "analytics_agent/context/glossary.yaml"
DEFAULT_MAX_CHARS = 1_200


def load_glossary(path: Path | str = DEFAULT_GLOSSARY) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"glossary not found: {path}\n"
            "expected analytics_agent/context/glossary.yaml"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    metrics = data.get("metrics")
    terms = data.get("terms")
    if metrics is None:
        metrics = {}
    if terms is None:
        terms = []
    if not isinstance(metrics, dict) or not isinstance(terms, list):
        raise ValueError(f"{path}: 'metrics' must be a mapping and 'terms' a list")
    return {"metrics": metrics, "terms": terms}


def render_glossary(
    glossary: Dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Render only the short forms; keep it under ~300 tokens."""
    lines: List[str] = ["## 指标（默认口径）"]
    for metric in glossary.get("metrics", {}).values():
        name = metric.get("name") or ""
        short = metric.get("short") or metric.get("definition") or ""
        if name and short:
            lines.append(f"- {name}：{short}")

    terms = glossary.get("terms") or []
    if terms:
        lines.append("")
        lines.append("## 术语")
        for item in terms:
            term = item.get("term") or ""
            short = item.get("short") or item.get("definition") or ""
            if term and short:
                lines.append(f"- {term}：{short}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…（指标已截断）"
    return text


def glossary_tokens(glossary: Dict[str, Any]) -> int:
    return estimate_tokens(render_glossary(glossary))
