"""
Langfuse observability provider.

Langfuse 是面向 LLM 应用的可观测性平台（trace / span / generation / score）。本 provider
把框架的 Span/Metric 映射到 Langfuse SDK：

- span -> Langfuse span（v3 ``start_span``，必要时回退到 v2 ``trace.span``）
- metric -> Langfuse score（数值型指标，如耗时、token 数）

所有调用都做了防御式处理：Langfuse 异常不会中断 Agent 主流程。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from vanna.core.observability import ObservabilityProvider, Span

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)


class LangfuseObservabilityProvider(ObservabilityProvider):
    """Observability provider backed by Langfuse."""

    def __init__(
        self,
        public_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        host: Optional[str] = None,
        *,
        client: Any = None,
        trace_name: str = "vanna",
        **client_kwargs: Any,
    ) -> None:
        if client is None:
            try:
                from langfuse import Langfuse
            except ImportError as e:  # pragma: no cover
                raise ImportError(
                    "langfuse package is required. "
                    "Install with: pip install 'vanna[langfuse]'"
                ) from e
            client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
                **client_kwargs,
            )

        self._client = client
        self._trace_name = trace_name
        self._trace_id: Optional[str] = None
        self._lf_spans: Dict[str, Any] = {}
        self._open_count = 0

    # ------------------------------------------------------------ trace id

    def _ensure_trace_id(self) -> Optional[str]:
        if self._trace_id is None:
            create = getattr(self._client, "create_trace_id", None)
            if callable(create):
                try:
                    self._trace_id = create()
                except Exception:
                    self._trace_id = None
        return self._trace_id

    # ------------------------------------------------------------ tracing

    async def create_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        span = Span(name=name, attributes=dict(attributes or {}))
        payload = {key: _jsonable(value) for key, value in (attributes or {}).items()}
        trace_id = self._ensure_trace_id()

        lf_span = None
        try:
            start_span = getattr(self._client, "start_span", None)
            if callable(start_span):
                trace_context = {"trace_id": trace_id} if trace_id else None
                try:
                    lf_span = start_span(
                        name=name,
                        input=payload or None,
                        metadata=payload or None,
                        trace_context=trace_context,
                    )
                except TypeError:
                    lf_span = start_span(
                        name=name, input=payload or None, metadata=payload or None
                    )
            else:
                # Langfuse v2 回退：trace -> span
                trace = self._client.trace(name=self._trace_name)
                lf_span = trace.span(name=name, metadata=payload or None)
        except Exception:
            logger.exception("Failed to start Langfuse span %s", name)

        if lf_span is not None:
            self._lf_spans[span.id] = lf_span
            self._open_count += 1
        return span

    async def end_span(self, span: Span) -> None:
        span.end()
        lf_span = self._lf_spans.pop(span.id, None)
        if lf_span is None:
            return
        self._open_count = max(0, self._open_count - 1)
        try:
            update = getattr(lf_span, "update", None)
            if callable(update):
                duration = span.duration_ms()
                try:
                    update(
                        metadata={
                            key: _jsonable(value)
                            for key, value in span.attributes.items()
                        },
                        output={"duration_ms": round(duration or 0.0, 3)},
                    )
                except TypeError:
                    update(metadata=span.attributes)
            end = getattr(lf_span, "end", None)
            if callable(end):
                end()
        except Exception:
            logger.exception("Failed to end Langfuse span %s", span.name)
        finally:
            # 根 span 结束后重置 trace，使下一次请求开启新的 trace
            if self._open_count == 0:
                self._trace_id = None

    # ------------------------------------------------------------ metrics

    async def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record a metric as a Langfuse score (best-effort)."""
        try:
            score_current = getattr(self._client, "score_current_trace", None)
            comment = f"unit={unit} tags={tags or {}}"
            if callable(score_current):
                score_current(name=name, value=float(value), comment=comment)
            elif self._trace_id:
                score = getattr(self._client, "score", None)
                if callable(score):
                    score(
                        trace_id=self._trace_id,
                        name=name,
                        value=float(value),
                        comment=comment,
                    )
        except Exception:
            logger.exception("Failed to record Langfuse score %s", name)

    # ------------------------------------------------------------ lifecycle

    def flush(self) -> None:
        flush = getattr(self._client, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                logger.exception("Failed to flush Langfuse client")
