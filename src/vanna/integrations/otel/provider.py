"""
OpenTelemetry observability provider.

OpenTelemetry（OTel）是厂商中立的可观测性标准：只要把 span/metric 交给 OTel SDK，
就能通过 OTLP 导出到任意后端（Jaeger、Tempo、Grafana、Datadog、Langfuse OTLP 等）。

两种使用方式：
1. 传入现成的 ``tracer`` / ``meter``（推荐：由应用统一配置 OTel SDK 与 exporter）；
2. 传入 ``endpoint``，本类会自动装配 OTLP HTTP/gRPC exporter（需要 ``vanna[otel]``）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from vanna.core.observability import ObservabilityProvider, Span

logger = logging.getLogger(__name__)


def _coerce_attr(value: Any) -> Any:
    """OTel 属性只接受 str/bool/int/float 或同类序列；其余转 JSON 字符串。"""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(v, (str, bool, int, float)) for v in value
    ):
        return list(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


class OpenTelemetryObservabilityProvider(ObservabilityProvider):
    """Observability provider backed by OpenTelemetry (traces + metrics)."""

    def __init__(
        self,
        tracer: Any = None,
        meter: Any = None,
        *,
        service_name: str = "vanna",
        endpoint: Optional[str] = None,
        protocol: str = "http/protobuf",
        headers: Optional[Dict[str, str]] = None,
        resource_attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            from opentelemetry import metrics as otel_metrics
            from opentelemetry import trace as otel_trace
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "opentelemetry packages are required. "
                "Install with: pip install 'vanna[otel]'"
            ) from e

        # 先初始化，供 _configure_sdk 记录自动装配的 provider
        self._tracer_provider = None
        self._meter_provider = None

        if (tracer is None or meter is None) and endpoint:
            tracer, meter = self._configure_sdk(
                service_name=service_name,
                endpoint=endpoint,
                protocol=protocol,
                headers=headers or {},
                resource_attributes=resource_attributes or {},
            )

        self._tracer = tracer or otel_trace.get_tracer(service_name)
        self._meter = meter or otel_metrics.get_meter(service_name)
        self._otel_spans: Dict[str, Any] = {}
        self._instruments: Dict[tuple, Any] = {}

    # ------------------------------------------------------------- SDK setup

    def _configure_sdk(
        self,
        *,
        service_name: str,
        endpoint: str,
        protocol: str,
        headers: Dict[str, str],
        resource_attributes: Dict[str, Any],
    ):
        try:
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "opentelemetry-sdk is required for endpoint auto-configuration. "
                "Install with: pip install 'vanna[otel]'"
            ) from e

        span_exporter_cls, metric_exporter_cls = self._load_exporters(protocol)
        resource = Resource.create(
            {"service.name": service_name, **resource_attributes}
        )

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(span_exporter_cls(endpoint=endpoint, headers=headers))
        )
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter_cls(endpoint=endpoint, headers=headers)
        )
        meter_provider = MeterProvider(
            resource=resource, metric_readers=[metric_reader]
        )

        # 注册为全局 provider（便于其它库复用）
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace

        otel_trace.set_tracer_provider(tracer_provider)
        otel_metrics.set_meter_provider(meter_provider)

        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        return (
            tracer_provider.get_tracer(service_name),
            meter_provider.get_meter(service_name),
        )

    @staticmethod
    def _load_exporters(protocol: str):
        normalized = (protocol or "").lower().replace("_", "/")
        try:
            if normalized in {"grpc"}:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                    OTLPMetricExporter,
                )
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
            else:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                    OTLPMetricExporter,
                )
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "OTLP exporters are required. Install with: pip install 'vanna[otel]'"
            ) from e
        return OTLPSpanExporter, OTLPMetricExporter

    # ------------------------------------------------------------ tracing

    async def create_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        span = Span(name=name, attributes=dict(attributes or {}))
        try:
            otel_span = self._tracer.start_span(
                name,
                attributes={
                    key: _coerce_attr(value)
                    for key, value in (attributes or {}).items()
                },
            )
            self._otel_spans[span.id] = otel_span
        except Exception:
            logger.exception("Failed to start OpenTelemetry span %s", name)
        return span

    async def end_span(self, span: Span) -> None:
        span.end()
        otel_span = self._otel_spans.pop(span.id, None)
        if otel_span is None:
            return
        try:
            for key, value in span.attributes.items():
                otel_span.set_attribute(key, _coerce_attr(value))
            duration = span.duration_ms()
            if duration is not None:
                otel_span.set_attribute("duration_ms", duration)
            otel_span.end()
        except Exception:
            logger.exception("Failed to end OpenTelemetry span %s", span.name)

    # ------------------------------------------------------------ metrics

    def _instrument(self, name: str, unit: str):
        key = (name, unit)
        instrument = self._instruments.get(key)
        if instrument is None:
            if unit == "count":
                instrument = self._meter.create_counter(
                    name=name, unit="1", description=f"{name} counter"
                )
            else:
                instrument = self._meter.create_histogram(
                    name=name, unit=unit or "1", description=f"{name} histogram"
                )
            self._instruments[key] = instrument
        return instrument

    async def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            attributes = {
                key: _coerce_attr(val) for key, val in (tags or {}).items()
            }
            instrument = self._instrument(name, unit)
            if unit == "count" and hasattr(instrument, "add"):
                instrument.add(float(value), attributes=attributes)
            elif hasattr(instrument, "record"):
                instrument.record(float(value), attributes=attributes)
            else:  # pragma: no cover - 兼容极端自定义 meter
                instrument.add(float(value), attributes=attributes)
        except Exception:
            logger.exception("Failed to record OpenTelemetry metric %s", name)

    # ------------------------------------------------------------ lifecycle

    def force_flush(self, timeout_millis: int = 30000) -> None:
        """Flush buffered telemetry (call before process exit if needed)."""
        for provider in (self._tracer_provider, self._meter_provider):
            flush = getattr(provider, "force_flush", None)
            if flush is not None:
                try:
                    flush(timeout_millis=timeout_millis)
                except TypeError:  # pragma: no cover - 不同版本签名差异
                    flush()
                except Exception:
                    logger.exception("Failed to flush OpenTelemetry provider")
