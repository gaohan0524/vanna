"""
OpenTelemetry integration.

提供基于 OTel 的可观测性 provider：span/metric 可由 OTLP 导出到任意标准后端。
"""

from .provider import OpenTelemetryObservabilityProvider

__all__ = ["OpenTelemetryObservabilityProvider"]
