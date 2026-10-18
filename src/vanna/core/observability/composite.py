"""
Composite observability provider (fan-out to multiple backends).

把同一份遥测数据同时上报到多个后端（例如 OTel + Langfuse + LangSmith）。任一后端
异常都不会影响其它后端，也不会中断 Agent 主流程。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .base import ObservabilityProvider
from .models import Span

logger = logging.getLogger(__name__)


class CompositeObservabilityProvider(ObservabilityProvider):
    """Fan out telemetry to multiple ObservabilityProviders."""

    def __init__(self, providers: List[ObservabilityProvider]) -> None:
        self.providers = list(providers)
        # 共享 Span(id) -> [(子 provider, 子 span)]
        self._children: Dict[str, List[Tuple[ObservabilityProvider, Span]]] = {}

    async def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        for provider in self.providers:
            try:
                await provider.record_metric(name, value, unit=unit, tags=tags)
            except Exception:
                logger.exception(
                    "Observability provider %s failed to record metric %s",
                    type(provider).__name__,
                    name,
                )

    async def create_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        span = Span(name=name, attributes=dict(attributes or {}))
        children: List[Tuple[ObservabilityProvider, Span]] = []
        for provider in self.providers:
            try:
                child = await provider.create_span(
                    name, attributes=dict(attributes or {})
                )
                children.append((provider, child))
            except Exception:
                logger.exception(
                    "Observability provider %s failed to create span %s",
                    type(provider).__name__,
                    name,
                )
        self._children[span.id] = children
        return span

    async def end_span(self, span: Span) -> None:
        span.end()
        children = self._children.pop(span.id, [])
        for provider, child in children:
            try:
                # 把调用方在共享 Span 上后置设置的属性同步给子 span
                for key, value in span.attributes.items():
                    child.set_attribute(key, value)
                await provider.end_span(child)
            except Exception:
                logger.exception(
                    "Observability provider %s failed to end span %s",
                    type(provider).__name__,
                    span.name,
                )
