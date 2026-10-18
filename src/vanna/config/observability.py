"""
Observability configuration & provider factory.

把「用哪个可观测性后端」做成可配置项，支持灵活选择或同时启用多个：

- ``none``      不启用（默认，零开销）
- ``logging``   标准库 logging（零依赖，本地排障）
- ``otel``      OpenTelemetry（厂商中立标准，OTLP 导出到任意后端）
- ``langfuse``  Langfuse 原生 SDK
- ``langsmith`` LangSmith 原生 SDK
- ``composite`` 同时向多个后端扇出（例如 otel + langfuse）

配置来源：Python 参数 / 服务端 JSON 的 ``observability`` 段 / 环境变量。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.observability import (
    CompositeObservabilityProvider,
    ObservabilityProvider,
)

VALID_OBSERVABILITY_BACKENDS = {
    "none",
    "logging",
    "otel",
    "langfuse",
    "langsmith",
    "composite",
}


def _parse_headers(value: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for pair in (value or "").split(","):
        if "=" in pair:
            key, _, val = pair.partition("=")
            if key.strip():
                headers[key.strip()] = val.strip()
    return headers


@dataclass
class ObservabilityConfig:
    """Configuration for observability providers."""

    backend: str = "none"
    # composite 模式下要启用的后端列表
    backends: List[str] = field(default_factory=list)
    service_name: str = "vanna"

    # OpenTelemetry
    otel_endpoint: Optional[str] = None
    otel_protocol: str = "http/protobuf"
    otel_headers: Dict[str, str] = field(default_factory=dict)

    # Langfuse
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_host: Optional[str] = None

    # LangSmith
    langsmith_api_key: Optional[str] = None
    langsmith_api_url: Optional[str] = None
    langsmith_project: Optional[str] = None

    # 传给后端构造器的额外参数（可与 backend_options 二选一）
    options: Dict[str, Any] = field(default_factory=dict)
    # 按后端名分别提供额外参数（composite 时很有用）
    backend_options: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------- resolution

    def validate(self) -> "ObservabilityConfig":
        backend = self.backend.lower()
        if backend not in VALID_OBSERVABILITY_BACKENDS:
            raise ValueError(
                f"Unsupported observability backend: {backend!r}. "
                f"Choose from {sorted(VALID_OBSERVABILITY_BACKENDS)}"
            )
        if backend == "composite":
            for item in self.backends:
                if item.lower() not in VALID_OBSERVABILITY_BACKENDS:
                    raise ValueError(
                        f"Unsupported observability backend in backends: {item!r}"
                    )
        return self

    def options_for(self, backend: str) -> Dict[str, Any]:
        specific = self.backend_options.get(backend)
        if specific is not None:
            return dict(specific)
        return dict(self.options)

    # ------------------------------------------------------------- constructors

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "ObservabilityConfig":
        raw = dict(data or {})
        options = dict(raw.pop("options", None) or {})
        backend_options = dict(raw.pop("backend_options", None) or {})

        known = {
            "backend",
            "backends",
            "service_name",
            "otel_endpoint",
            "otel_protocol",
            "otel_headers",
            "langfuse_public_key",
            "langfuse_secret_key",
            "langfuse_host",
            "langsmith_api_key",
            "langsmith_api_url",
            "langsmith_project",
            "options",
            "backend_options",
        }
        for key in list(raw.keys()):
            if key not in known:
                options[key] = raw.pop(key)

        backends = raw.get("backends") or []
        if isinstance(backends, str):
            backends = [b.strip() for b in backends.split(",") if b.strip()]

        return cls(
            backend=str(raw.get("backend", "none")).lower(),
            backends=[str(b).lower() for b in backends],
            service_name=raw.get("service_name", "vanna"),
            otel_endpoint=raw.get("otel_endpoint"),
            otel_protocol=raw.get("otel_protocol", "http/protobuf"),
            otel_headers=dict(raw.get("otel_headers") or {}),
            langfuse_public_key=raw.get("langfuse_public_key"),
            langfuse_secret_key=raw.get("langfuse_secret_key"),
            langfuse_host=raw.get("langfuse_host"),
            langsmith_api_key=raw.get("langsmith_api_key"),
            langsmith_api_url=raw.get("langsmith_api_url"),
            langsmith_project=raw.get("langsmith_project"),
            options=options,
            backend_options=backend_options,
        )

    @classmethod
    def from_env(
        cls,
        prefix: str = "VANNA_OBSERVABILITY",
        environ: Optional[Mapping[str, str]] = None,
    ) -> "ObservabilityConfig":
        env = environ if environ is not None else os.environ

        def get(name: str, default: Optional[str] = None) -> Optional[str]:
            return env.get(f"{prefix}_{name}", default)

        backends_raw = get("BACKENDS") or ""
        backends = [b.strip().lower() for b in backends_raw.split(",") if b.strip()]

        return cls(
            backend=(get("BACKEND") or "none").lower(),
            backends=backends,
            service_name=get("SERVICE_NAME") or "vanna",
            otel_endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
            otel_protocol=env.get("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/protobuf",
            otel_headers=_parse_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS")),
            langfuse_public_key=env.get("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=env.get("LANGFUSE_SECRET_KEY"),
            langfuse_host=env.get("LANGFUSE_HOST"),
            langsmith_api_key=env.get("LANGSMITH_API_KEY")
            or env.get("LANGCHAIN_API_KEY"),
            langsmith_api_url=env.get("LANGSMITH_ENDPOINT")
            or env.get("LANGCHAIN_ENDPOINT"),
            langsmith_project=env.get("LANGSMITH_PROJECT")
            or env.get("LANGCHAIN_PROJECT"),
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "backend": self.backend,
            "backends": self.backends,
            "service_name": self.service_name,
            "otel_endpoint": self.otel_endpoint,
            "otel_protocol": self.otel_protocol,
            "otel_headers": self.otel_headers,
            "langfuse_public_key": self.langfuse_public_key,
            "langfuse_secret_key": self.langfuse_secret_key,
            "langfuse_host": self.langfuse_host,
            "langsmith_api_key": self.langsmith_api_key,
            "langsmith_api_url": self.langsmith_api_url,
            "langsmith_project": self.langsmith_project,
            "options": dict(self.options),
            "backend_options": dict(self.backend_options),
        }
        return {k: v for k, v in data.items() if v is not None}


def _coerce_config(config: Any) -> ObservabilityConfig:
    if config is None:
        return ObservabilityConfig()
    if isinstance(config, ObservabilityConfig):
        return config
    if isinstance(config, Mapping):
        return ObservabilityConfig.from_dict(config)
    raise TypeError(
        "observability config must be an ObservabilityConfig, a mapping, or None; "
        f"got {type(config).__name__}"
    )


# --------------------------------------------------------------------- factory


def _create_single(
    backend: str, cfg: ObservabilityConfig
) -> Optional[ObservabilityProvider]:
    backend = backend.lower()
    options = cfg.options_for(backend)

    if backend == "none":
        return None

    if backend == "logging":
        from ..integrations.local.observability import LoggingObservabilityProvider

        return LoggingObservabilityProvider(**options)

    if backend == "otel":
        from ..integrations.otel import OpenTelemetryObservabilityProvider

        kwargs: Dict[str, Any] = {
            "service_name": cfg.service_name,
            "endpoint": cfg.otel_endpoint,
            "protocol": cfg.otel_protocol,
            "headers": cfg.otel_headers,
        }
        kwargs.update({k: v for k, v in options.items() if k not in kwargs})
        return OpenTelemetryObservabilityProvider(**kwargs)

    if backend == "langfuse":
        from ..integrations.langfuse import LangfuseObservabilityProvider

        kwargs = {
            "public_key": cfg.langfuse_public_key,
            "secret_key": cfg.langfuse_secret_key,
            "host": cfg.langfuse_host,
        }
        kwargs.update({k: v for k, v in options.items() if k not in kwargs})
        return LangfuseObservabilityProvider(**kwargs)

    if backend == "langsmith":
        from ..integrations.langsmith import LangSmithObservabilityProvider

        kwargs = {
            "api_key": cfg.langsmith_api_key,
            "api_url": cfg.langsmith_api_url,
            "project_name": cfg.langsmith_project,
        }
        kwargs.update({k: v for k, v in options.items() if k not in kwargs})
        return LangSmithObservabilityProvider(**kwargs)

    raise ValueError(f"Unsupported observability backend: {backend!r}")


def create_observability_provider(
    config: Any = None,
) -> Optional[ObservabilityProvider]:
    """Create the configured observability provider (or None when disabled)."""
    cfg = _coerce_config(config).validate()

    if cfg.backend.lower() == "composite":
        providers = [
            provider
            for backend in cfg.backends
            if (provider := _create_single(backend, cfg)) is not None
        ]
        return CompositeObservabilityProvider(providers) if providers else None

    return _create_single(cfg.backend, cfg)


def apply_observability_config(agent: Any, config: Any = None) -> Any:
    """Apply an observability config to an existing Agent in place."""
    agent.observability_provider = create_observability_provider(config)
    return agent
