"""
Base observability provider interface.

Observability providers allow you to collect telemetry data about
agent execution for monitoring and debugging.

本模块定义可观测性提供者的抽象基类。它是「横切面」扩展点之一：不传 provider 时
Agent 全程零埋点开销（所有方法空实现），传入后即可把 span/metric 上报到任意监控
后端（Prometheus、OpenTelemetry、日志等）。
"""

from abc import ABC
from typing import Any, Dict, Optional

from .models import Span, Metric


class ObservabilityProvider(ABC):
    """Provider for collecting telemetry and observability data.

    Subclass this to create custom observability integrations that can:
    - Emit metrics to monitoring systems
    - Create distributed traces
    - Log performance data
    - Track costs and usage
    - Monitor error rates

    Example:
        class PrometheusProvider(ObservabilityProvider):
            def __init__(self, registry):
                self.registry = registry
                self.request_counter = Counter(
                    'agent_requests_total',
                    'Total agent requests',
                    registry=registry
                )

            async def record_metric(self, name: str, value: float, tags: Dict[str, str]) -> None:
                if name == "agent.request":
                    self.request_counter.inc()

            async def create_span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> Span:
                span = Span(name=name, attributes=attributes or {})
                return span

        agent = AgentRunner(
            llm_service=...,
            observability_provider=PrometheusProvider(registry)
        )

    可观测性提供者的抽象基类。所有方法都是空实现，因此不传该扩展点时框架行为
    完全不变（零开销）。Agent 在整条链路的每个阶段都会调用 create_span / end_span /
    record_metric，你只需重写关心的方法即可把遥测数据接入外部系统。
    """

    async def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record a metric measurement.

        Args:
            name: Metric name (e.g., "agent.request.duration")
            value: Metric value
            unit: Unit of measurement (e.g., "ms", "tokens")
            tags: Additional tags/labels for the metric

        记录一次指标测量（默认空实现，子类可上报到监控系统）。
        """
        pass

    async def create_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        """Create a new span for tracing.

        Args:
            name: Span name/operation
            attributes: Initial span attributes

        Returns:
            Span object to track the operation

        Note:
            Call span.end() when the operation completes.

        创建一个追踪 span（默认返回本地 Span 对象，不上报）。调用方应在操作完成后
        调用 end_span 或 span.end() 关闭它。
        """
        return Span(name=name, attributes=attributes or {})

    async def end_span(self, span: Span) -> None:
        """End a span and record it.

        Args:
            span: The span to end

        结束并记录一个 span（默认实现仅调用 span.end() 标记结束时间）。
        """
        span.end()
