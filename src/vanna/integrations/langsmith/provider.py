"""
LangSmith observability provider.

LangSmith 是 LangChain 生态的 tracing / evaluation 平台。本 provider 把框架的 Span
映射为 LangSmith 的 ``RunTree``（run_type=chain）：create_span -> post，end_span -> patch。

LangSmith 以 trace（run 树）为核心，没有与框架 Metric 一一对应的概念，因此
``record_metric`` 仅做 best-effort：若当前存在 run，则作为 feedback 写入；否则忽略。
所有调用都做了防御式处理，异常不会中断 Agent。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from vanna.core.observability import ObservabilityProvider, Span

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)


class LangSmithObservabilityProvider(ObservabilityProvider):
    """Observability provider backed by LangSmith runs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        project_name: Optional[str] = None,
        *,
        client: Any = None,
        **client_kwargs: Any,
    ) -> None:
        if client is None:
            try:
                from langsmith import Client
            except ImportError as e:  # pragma: no cover
                raise ImportError(
                    "langsmith package is required. "
                    "Install with: pip install 'vanna[langsmith]'"
                ) from e
            client = Client(
                api_key=api_key, api_url=api_url, **client_kwargs
            )

        self._client = client
        self._project_name = project_name
        self._runs: Dict[str, Any] = {}
        self._last_run_id: Optional[str] = None

    async def create_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        span = Span(name=name, attributes=dict(attributes or {}))
        payload = {key: _jsonable(value) for key, value in (attributes or {}).items()}
        try:
            from langsmith.run_trees import RunTree

            run = RunTree(
                name=name,
                run_type="chain",
                inputs=payload or {},
                extra={"metadata": payload},
                client=self._client,
                project_name=self._project_name,
            )
            run.post()
            self._runs[span.id] = run
            self._last_run_id = str(getattr(run, "id", "")) or self._last_run_id
        except Exception:
            logger.exception("Failed to create LangSmith run %s", name)
        return span

    async def end_span(self, span: Span) -> None:
        span.end()
        run = self._runs.pop(span.id, None)
        if run is None:
            return
        try:
            duration = span.duration_ms()
            run.end(
                outputs={
                    "duration_ms": round(duration or 0.0, 3),
                    "attributes": {
                        key: _jsonable(value)
                        for key, value in span.attributes.items()
                    },
                }
            )
            run.patch()
        except Exception:
            logger.exception("Failed to end LangSmith run %s", span.name)

    async def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Best-effort: attach the metric as LangSmith feedback on the latest run."""
        if not self._last_run_id:
            return
        create_feedback = getattr(self._client, "create_feedback", None)
        if not callable(create_feedback):
            return
        try:
            create_feedback(
                run_id=self._last_run_id,
                key=name,
                score=float(value),
                comment=f"unit={unit} tags={tags or {}}",
            )
        except Exception:
            logger.exception("Failed to record LangSmith feedback %s", name)

    def flush(self) -> None:
        flush = getattr(self._client, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                logger.exception("Failed to flush LangSmith client")
