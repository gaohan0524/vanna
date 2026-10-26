"""Load and render L2 metrics/terms (glossary.yaml)."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from analytics_agent.config import PROJECT_ROOT

from .render import estimate_tokens

DEFAULT_GLOSSARY = PROJECT_ROOT / "analytics_agent/context/glossary.yaml"
DEFAULT_MAX_CHARS = 1_200

# 常见业务叫法 -> glossary 的 key。
# 只做“同义词映射”，不做语义推理；新增指标叫法时在这里补一行。
_METRIC_ALIASES: Dict[str, str] = {
    "gmv": "gmv",
    "成交额": "gmv",
    "销售额": "gmv",
    "交易额": "gmv",
    "净收入": "net_revenue",
    "净营收": "net_revenue",
    "客单价": "aov",
    "aov": "aov",
    "平均订单金额": "aov",
    "支付金额": "payment_amount",
    "到账金额": "payment_amount",
    "退款": "refund_amount",
    "退款金额": "refund_amount",
    "复购": "repurchase_rate",
    "复购率": "repurchase_rate",
    "转化率": "conversion_rate",
    "支付转化率": "conversion_rate",
    "留存": "retention_d7",
    "留存率": "retention_d7",
    "7日留存": "retention_d7",
    "mrr": "mrr",
    "月度经常性收入": "mrr",
}


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


def _norm(value: str) -> str:
    return value.strip().casefold()


def _display(metric: Dict[str, Any], key: str) -> str:
    return str(metric.get("name") or key)


def find_metric(
    glossary: Dict[str, Any], name: str
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """按 key / 中文名 / 别名查找指标，返回 ``(key, metric)``。

    匹配顺序：
    1. key 或 name 精确匹配（大小写不敏感）；
    2. `_METRIC_ALIASES` 同义词；
    3. 查询里包含 key/name（如“各渠道 GMV 是多少”）；
    4. 多个候选时返回 ``None``，由调用方列出候选，避免猜错。
    """
    query = _norm(name)
    if not query:
        return None

    metrics = glossary.get("metrics", {})
    for key, metric in metrics.items():
        if query in {_norm(str(key)), _norm(_display(metric, key))}:
            return key, metric

    target = _METRIC_ALIASES.get(query)
    if target and target in metrics:
        return target, metrics[target]

    matches: List[Tuple[str, Dict[str, Any]]] = []
    for key, metric in metrics.items():
        for token in (_norm(str(key)), _norm(_display(metric, key))):
            if len(token) >= 2 and token in query:
                matches.append((key, metric))
                break

    if len(matches) == 1:
        return matches[0]
    return None


def suggest_metrics(glossary: Dict[str, Any], name: str, limit: int = 5) -> List[str]:
    """为未命中的指标名给候选：编辑距离 + 包含关系，返回展示名。"""
    query = _norm(name)
    metrics = glossary.get("metrics", {})
    display_by_token: Dict[str, str] = {}
    for key, metric in metrics.items():
        display_by_token[_norm(str(key))] = _display(metric, key)
        display_by_token[_norm(_display(metric, key))] = _display(metric, key)

    suggestions: List[str] = []
    for token in difflib.get_close_matches(
        query, list(display_by_token), n=limit, cutoff=0.4
    ):
        display = display_by_token[token]
        if display not in suggestions:
            suggestions.append(display)

    if not suggestions:
        for token, display in display_by_token.items():
            if query and (query in token or token in query):
                if display not in suggestions:
                    suggestions.append(display)

    if not suggestions:
        for metric in metrics.values():
            display = _display(metric, "")
            if display and display not in suggestions:
                suggestions.append(display)
    return suggestions[:limit]


def list_metric_names(glossary: Dict[str, Any]) -> List[str]:
    return [
        _display(metric, key) for key, metric in glossary.get("metrics", {}).items()
    ]


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
