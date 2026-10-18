"""
Zero-dependency logging observability provider.

把 span / metric 以日志形式输出，适合本地开发与最小化排障；生产环境建议改用
OpenTelemetry / Langfuse / LangSmith（见 vanna.integrations.otel / langfuse / langsmith）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from vanna.core.observability import ObservabilityProvider, Span


class LoggingObservabilityProvider(ObservabilityProvider):
    """Observability provider that writes spans/metrics to a Python logger."""

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        level: int = logging.INFO,
    ) -> None:
        self._logger = logger or logging.getLogger("vanna.observability")
        self._level = level

    async def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        self._logger.log(
            self._level,
            "metric name=%s value=%s unit=%s tags=%s",
            name,
            value,
            unit,
            tags or {},
        )

    async def create_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        span = Span(name=name, attributes=dict(attributes or {}))
        self._logger.log(
            self._level,
            "span.start name=%s id=%s attributes=%s",
            name,
            span.id,
            span.attributes,
        )
        return span

    async def end_span(self, span: Span) -> None:
        span.end()
        self._logger.log(
            self._level,
            "span.end name=%s id=%s duration_ms=%s attributes=%s",
            span.name,
            span.id,
            round(span.duration_ms() or 0.0, 3),
            span.attributes,
        )
