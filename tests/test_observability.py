"""
Observability integrations tests.

覆盖：复合 provider 扇出与故障隔离、logging provider、OTel provider（假对象 + 真实
SDK InMemory exporter）、Langfuse provider（假 client）、配置解析与工厂分发。
"""

import logging
from types import SimpleNamespace

import pytest

from vanna.config import (
    ObservabilityConfig,
    apply_observability_config,
    create_observability_provider,
)
from vanna.core.observability import (
    CompositeObservabilityProvider,
    ObservabilityProvider,
    Span,
)
from vanna.integrations.local.observability import LoggingObservabilityProvider
from vanna.integrations.otel import OpenTelemetryObservabilityProvider


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _FakeProvider(ObservabilityProvider):
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.metrics = []
        self.spans = []

    async def record_metric(self, name, value, unit="", tags=None):
        if self.fail:
            raise RuntimeError("boom")
        self.metrics.append((name, value, unit, tags))

    async def create_span(self, name, attributes=None):
        if self.fail:
            raise RuntimeError("boom")
        span = Span(name=name, attributes=dict(attributes or {}))
        self.spans.append(span)
        return span

    async def end_span(self, span):
        if self.fail:
            raise RuntimeError("boom")
        span.end()


class _FakeOtelSpan:
    def __init__(self) -> None:
        self.attributes = {}
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        self.ended = True


class _FakeTracer:
    def __init__(self) -> None:
        self.started = []

    def start_span(self, name, attributes=None):
        span = _FakeOtelSpan()
        self.started.append((name, span))
        return span


class _FakeInstrument:
    def __init__(self) -> None:
        self.records = []

    def record(self, value, attributes=None):
        self.records.append((value, attributes))

    def add(self, value, attributes=None):
        self.records.append((value, attributes))


class _FakeMeter:
    def __init__(self) -> None:
        self.instruments = {}

    def _make(self, name):
        instrument = _FakeInstrument()
        self.instruments[name] = instrument
        return instrument

    def create_counter(self, name, unit="", description=""):
        return self._make(name)

    def create_histogram(self, name, unit="", description=""):
        return self._make(name)


# --------------------------------------------------------------------------- #
# composite
# --------------------------------------------------------------------------- #
async def test_composite_fans_out_metrics_and_spans():
    a, b = _FakeProvider(), _FakeProvider()
    composite = CompositeObservabilityProvider([a, b])

    span = await composite.create_span("op", {"x": 1})
    span.set_attribute("y", 2)
    await composite.end_span(span)

    assert len(a.spans) == 1 and len(b.spans) == 1
    assert a.spans[0].attributes["y"] == 2
    assert a.spans[0].end_time is not None

    await composite.record_metric("m", 1.0, unit="ms", tags={"k": "v"})
    assert a.metrics == [("m", 1.0, "ms", {"k": "v"})]
    assert b.metrics == [("m", 1.0, "ms", {"k": "v"})]


async def test_composite_isolates_failures():
    good, bad = _FakeProvider(), _FakeProvider(fail=True)
    composite = CompositeObservabilityProvider([bad, good])

    await composite.record_metric("m", 1.0)
    span = await composite.create_span("op", {})
    await composite.end_span(span)

    assert len(good.spans) == 1
    assert good.metrics == [("m", 1.0, "", None)]


# --------------------------------------------------------------------------- #
# logging provider
# --------------------------------------------------------------------------- #
async def test_logging_provider(caplog):
    provider = LoggingObservabilityProvider()
    with caplog.at_level(logging.INFO, logger="vanna.observability"):
        span = await provider.create_span("op", {"a": 1})
        await provider.end_span(span)
        await provider.record_metric("m", 1.0, unit="count", tags={"k": "v"})
    text = caplog.text
    assert "span.start" in text
    assert "span.end" in text
    assert "metric name=m" in text


# --------------------------------------------------------------------------- #
# OpenTelemetry provider
# --------------------------------------------------------------------------- #
async def test_otel_provider_with_fake_tracer_and_meter():
    tracer = _FakeTracer()
    meter = _FakeMeter()
    provider = OpenTelemetryObservabilityProvider(tracer=tracer, meter=meter)

    span = await provider.create_span("op", {"a": 1, "obj": {"nested": True}})
    span.set_attribute("b", 2)
    await provider.end_span(span)

    assert len(tracer.started) == 1
    otel_span = tracer.started[0][1]
    assert otel_span.ended is True
    assert otel_span.attributes["b"] == 2
    assert "duration_ms" in otel_span.attributes
    # 非原生类型被序列化为字符串
    assert isinstance(otel_span.attributes["obj"], str)

    await provider.record_metric("tokens", 10, unit="count", tags={"model": "x"})
    await provider.record_metric("latency", 12.5, unit="ms")
    assert meter.instruments["tokens"].records[0][0] == 10
    assert meter.instruments["latency"].records[0][0] == 12.5


async def test_otel_provider_exports_real_span():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider = OpenTelemetryObservabilityProvider(
        tracer=tracer_provider.get_tracer("test")
    )

    span = await provider.create_span("op", {"a": 1})
    span.set_attribute("b", 2)
    await provider.end_span(span)

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "op"
    assert finished[0].attributes["a"] == 1
    assert finished[0].attributes["b"] == 2
    assert "duration_ms" in finished[0].attributes


# --------------------------------------------------------------------------- #
# Langfuse provider (fake client, no SDK/network required)
# --------------------------------------------------------------------------- #
class _FakeLfSpan:
    def __init__(self) -> None:
        self.updates = []
        self.ended = False

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.spans = []
        self.scores = []

    def create_trace_id(self):
        return "trace-1"

    def start_span(self, name, input=None, metadata=None, trace_context=None):
        span = _FakeLfSpan()
        self.spans.append((name, span, trace_context))
        return span

    def score_current_trace(self, name, value, comment=None):
        self.scores.append((name, value, comment))

    def flush(self):
        pass


async def test_langfuse_provider_maps_spans_and_scores():
    client = _FakeLangfuseClient()
    from vanna.integrations.langfuse import LangfuseObservabilityProvider

    provider = LangfuseObservabilityProvider(client=client)
    span = await provider.create_span("op", {"a": 1})
    span.set_attribute("b", 2)
    await provider.end_span(span)

    assert client.spans and client.spans[0][0] == "op"
    assert client.spans[0][2] == {"trace_id": "trace-1"}
    assert client.spans[0][1].ended is True

    await provider.record_metric("m", 3.0, unit="ms", tags={"k": "v"})
    assert client.scores and client.scores[0][0] == "m"


# --------------------------------------------------------------------------- #
# LangSmith provider (fake SDK module, no install/network required)
# --------------------------------------------------------------------------- #
async def test_langsmith_provider_maps_runs(monkeypatch):
    import sys
    import types

    class _FakeRun:
        def __init__(self, name, run_type, inputs, extra, client, project_name):
            self.name = name
            self.run_type = run_type
            self.inputs = inputs
            self.extra = extra
            self.id = "run-1"
            self.ended = False
            self.patched = False
            self.outputs = None

        def post(self):
            pass

        def end(self, outputs=None):
            self.ended = True
            self.outputs = outputs

        def patch(self):
            self.patched = True

    fake_pkg = types.ModuleType("langsmith")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_run_trees = types.ModuleType("langsmith.run_trees")
    fake_run_trees.RunTree = _FakeRun  # type: ignore[attr-defined]
    fake_pkg.run_trees = fake_run_trees  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", fake_pkg)
    monkeypatch.setitem(sys.modules, "langsmith.run_trees", fake_run_trees)

    class _FakeLsClient:
        def __init__(self):
            self.feedback = []

        def create_feedback(self, run_id, key, score, comment=None):
            self.feedback.append((run_id, key, score, comment))

        def flush(self):
            pass

    client = _FakeLsClient()
    from vanna.integrations.langsmith import LangSmithObservabilityProvider

    provider = LangSmithObservabilityProvider(client=client)
    span = await provider.create_span("op", {"a": 1})
    span.set_attribute("b", 2)
    await provider.end_span(span)

    await provider.record_metric("m", 1.0, unit="ms")
    assert client.feedback and client.feedback[0][1] == "m"


# --------------------------------------------------------------------------- #
# config & factory
# --------------------------------------------------------------------------- #
def test_observability_config_from_dict():
    cfg = ObservabilityConfig.from_dict(
        {
            "backend": "otel",
            "service_name": "svc",
            "otel_endpoint": "http://otel:4318",
            "custom_option": 1,
        }
    )
    assert cfg.backend == "otel"
    assert cfg.service_name == "svc"
    assert cfg.otel_endpoint == "http://otel:4318"
    assert cfg.options == {"custom_option": 1}


def test_observability_config_from_env():
    cfg = ObservabilityConfig.from_env(
        environ={
            "VANNA_OBSERVABILITY_BACKEND": "composite",
            "VANNA_OBSERVABILITY_BACKENDS": "otel,logging",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer x,X-Env=dev",
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGSMITH_API_KEY": "ls",
        }
    )
    assert cfg.backend == "composite"
    assert cfg.backends == ["otel", "logging"]
    assert cfg.otel_endpoint == "http://otel:4318"
    assert cfg.otel_headers == {"Authorization": "Bearer x", "X-Env": "dev"}
    assert cfg.langfuse_public_key == "pk"
    assert cfg.langsmith_api_key == "ls"


def test_observability_config_validate_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported"):
        ObservabilityConfig(backend="jaeger").validate()


def test_factory_none_returns_none():
    assert create_observability_provider({"backend": "none"}) is None


def test_factory_logging_backend():
    provider = create_observability_provider({"backend": "logging"})
    assert isinstance(provider, LoggingObservabilityProvider)


def test_factory_composite_backend():
    provider = create_observability_provider(
        {"backend": "composite", "backends": ["logging", "logging"]}
    )
    assert isinstance(provider, CompositeObservabilityProvider)
    assert len(provider.providers) == 2


def test_apply_observability_config_sets_agent_provider():
    agent = SimpleNamespace(observability_provider=None)
    apply_observability_config(agent, {"backend": "logging"})
    assert isinstance(agent.observability_provider, LoggingObservabilityProvider)


def test_fastapi_server_applies_observability_config():
    from vanna.servers.fastapi.app import VannaFastAPIServer

    agent = SimpleNamespace(observability_provider=None)
    VannaFastAPIServer(agent, {"observability": {"backend": "logging"}})
    assert isinstance(agent.observability_provider, LoggingObservabilityProvider)
