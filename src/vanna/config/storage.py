"""
Storage configuration & backend factory.

把「用哪个持久化后端」做成可配置项：支持 memory / sqlite / postgres / mysql / redis，
既可来自 Python 参数、字典（服务端 JSON 配置的 ``storage`` 段），也可来自环境变量。

示例：
    # 1) 显式构造
    from vanna.config import StorageConfig, create_stores
    conversation_store, agent_memory = create_stores(
        StorageConfig(backend="postgres", url="postgresql://...")
    )

    # 2) 环境变量
    #   VANNA_STORAGE_BACKEND=redis
    #   VANNA_STORAGE_URL=redis://localhost:6379/0
    cfg = StorageConfig.from_env()

    # 3) 服务端配置（JSON）中的 "storage" 段，由 VannaFastAPIServer/VannaFlaskServer 自动应用
    {"storage": {"backend": "sqlite", "sqlite_path": "vanna.db"}}
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from ..capabilities.agent_memory import AgentMemory
from ..core.storage import ConversationStore

VALID_BACKENDS = {"memory", "sqlite", "postgres", "mysql", "redis"}

# 从连接串 scheme 自动推断后端（仅当未显式指定 backend、且提供了 url 时）
_SCHEME_TO_BACKEND = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "mysql+pymysql": "mysql",
    "redis": "redis",
    "rediss": "redis",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class StorageConfig:
    """Configuration for conversation store and agent memory backends."""

    backend: str = "memory"
    # 可分别覆盖会话存储 / 记忆后端；为空则回落到 backend
    conversation_backend: Optional[str] = None
    memory_backend: Optional[str] = None
    # postgres/mysql/redis 的连接串（sqlite 用 sqlite_path）
    url: Optional[str] = None
    sqlite_path: str = "vanna.db"
    # 是否按租户（而非用户）隔离；True 时同租户用户共享数据
    tenant_sharing: bool = False
    # 可选：从 User 解析租户 id；默认使用 user.id
    tenant_resolver: Optional[Callable[[Any], str]] = None
    # 其余后端参数（如 host/database/user/password/port 等）
    options: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- resolution

    def _infer_backend(self, backend: str) -> str:
        """Infer the backend from ``url`` scheme when backend is left at the default.

        未显式指定 backend（仍为默认 memory）但提供了连接串时，按 url scheme 自动推断，
        这样只配置 ``VANNA_STORAGE_URL`` 就能选对后端。
        """
        if backend != "memory" or not self.url:
            return backend
        scheme = urlparse(self.url).scheme.lower()
        return _SCHEME_TO_BACKEND.get(scheme, backend)

    def resolved_conversation_backend(self) -> str:
        return self._infer_backend(
            (self.conversation_backend or self.backend).lower()
        )

    def resolved_memory_backend(self) -> str:
        return self._infer_backend((self.memory_backend or self.backend).lower())

    def validate(self) -> "StorageConfig":
        for label, backend in (
            ("conversation_backend", self.resolved_conversation_backend()),
            ("memory_backend", self.resolved_memory_backend()),
        ):
            if backend not in VALID_BACKENDS:
                raise ValueError(
                    f"Unsupported {label}: {backend!r}. "
                    f"Choose from {sorted(VALID_BACKENDS)}"
                )
        return self

    # ------------------------------------------------------------- constructors

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "StorageConfig":
        """Build from a plain dict (e.g. a JSON config ``storage`` section)."""
        raw = dict(data or {})
        options = dict(raw.pop("options", None) or {})

        known = {
            "backend",
            "conversation_backend",
            "memory_backend",
            "url",
            "sqlite_path",
            "tenant_sharing",
            "share_within_tenant",
            "tenant_resolver",
        }
        for key in list(raw.keys()):
            if key not in known:
                options[key] = raw.pop(key)

        tenant_sharing = raw.get(
            "tenant_sharing", raw.get("share_within_tenant", False)
        )
        return cls(
            backend=raw.get("backend", "memory"),
            conversation_backend=raw.get("conversation_backend"),
            memory_backend=raw.get("memory_backend"),
            url=raw.get("url"),
            sqlite_path=raw.get("sqlite_path", "vanna.db"),
            tenant_sharing=_as_bool(tenant_sharing),
            tenant_resolver=raw.get("tenant_resolver"),
            options=options,
        )

    @classmethod
    def from_env(
        cls, prefix: str = "VANNA_STORAGE", environ: Optional[Mapping[str, str]] = None
    ) -> "StorageConfig":
        """Build from environment variables.

        Supported keys (with ``prefix`` default ``VANNA_STORAGE``):
        ``BACKEND``, ``CONVERSATION_BACKEND``, ``MEMORY_BACKEND``, ``URL``,
        ``SQLITE_PATH``, ``TENANT_SHARING``.
        """
        env = environ if environ is not None else os.environ

        def get(name: str, default: Optional[str] = None) -> Optional[str]:
            return env.get(f"{prefix}_{name}", default)

        return cls(
            backend=(get("BACKEND") or "memory").lower(),
            conversation_backend=get("CONVERSATION_BACKEND"),
            memory_backend=get("MEMORY_BACKEND"),
            url=get("URL"),
            sqlite_path=get("SQLITE_PATH") or "vanna.db",
            tenant_sharing=_as_bool(get("TENANT_SHARING")),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (callables such as tenant_resolver are dropped)."""
        data: Dict[str, Any] = {
            "backend": self.backend,
            "conversation_backend": self.conversation_backend,
            "memory_backend": self.memory_backend,
            "url": self.url,
            "sqlite_path": self.sqlite_path,
            "tenant_sharing": self.tenant_sharing,
            "options": dict(self.options),
        }
        return {k: v for k, v in data.items() if v is not None}


def _coerce_config(config: Any) -> StorageConfig:
    if config is None:
        return StorageConfig()
    if isinstance(config, StorageConfig):
        return config
    if isinstance(config, Mapping):
        return StorageConfig.from_dict(config)
    raise TypeError(
        "storage config must be a StorageConfig, a mapping, or None; "
        f"got {type(config).__name__}"
    )


# --------------------------------------------------------------------- factory


def _mysql_params(cfg: StorageConfig) -> Dict[str, Any]:
    """Build PyMySQL connection params from a mysql:// URL or explicit options."""
    params = dict(cfg.options)
    if cfg.url:
        parsed = urlparse(cfg.url)
        if parsed.scheme not in ("mysql", "mysql+pymysql"):
            raise ValueError(
                f"MySQL URL must start with mysql://, got {cfg.url!r}"
            )
        if parsed.hostname:
            params.setdefault("host", parsed.hostname)
        if parsed.port:
            params.setdefault("port", parsed.port)
        if parsed.username:
            params.setdefault("user", unquote(parsed.username))
        if parsed.password:
            params.setdefault("password", unquote(parsed.password))
        if parsed.path and parsed.path.strip("/"):
            params.setdefault("database", parsed.path.lstrip("/"))
    return params


def _common_kwargs(cfg: StorageConfig) -> Dict[str, Any]:
    return {
        "tenant_resolver": cfg.tenant_resolver,
        "share_within_tenant": cfg.tenant_sharing,
    }


def create_conversation_store(config: Any = None) -> ConversationStore:
    """Create the configured conversation store backend."""
    cfg = _coerce_config(config).validate()
    backend = cfg.resolved_conversation_backend()

    if backend == "memory":
        from ..integrations.local import MemoryConversationStore

        return MemoryConversationStore()

    if backend == "sqlite":
        from ..integrations.sqlite import SQLiteConversationStore

        return SQLiteConversationStore(cfg.sqlite_path, **_common_kwargs(cfg))

    if backend == "postgres":
        from ..integrations.postgres import PostgresConversationStore

        return PostgresConversationStore(
            cfg.url, **_common_kwargs(cfg), **cfg.options
        )

    if backend == "mysql":
        from ..integrations.mysql import MySQLConversationStore

        return MySQLConversationStore(**_mysql_params(cfg), **_common_kwargs(cfg))

    if backend == "redis":
        from ..integrations.redis import RedisConversationStore

        return RedisConversationStore(
            cfg.url or "redis://localhost:6379/0",
            **_common_kwargs(cfg),
            **cfg.options,
        )

    raise ValueError(f"Unsupported conversation backend: {backend!r}")


def create_agent_memory(config: Any = None) -> AgentMemory:
    """Create the configured agent memory backend."""
    cfg = _coerce_config(config).validate()
    backend = cfg.resolved_memory_backend()

    if backend == "memory":
        # 配置驱动的内存后端默认开启用户隔离，避免多用户串号
        from ..integrations.local.agent_memory import DemoAgentMemory

        return DemoAgentMemory(user_scoped=True)

    if backend == "sqlite":
        from ..integrations.sqlite import SQLiteAgentMemory

        return SQLiteAgentMemory(cfg.sqlite_path, **_common_kwargs(cfg))

    if backend == "postgres":
        from ..integrations.postgres import PostgresAgentMemory

        return PostgresAgentMemory(cfg.url, **_common_kwargs(cfg), **cfg.options)

    if backend == "mysql":
        from ..integrations.mysql import MySQLAgentMemory

        return MySQLAgentMemory(**_mysql_params(cfg), **_common_kwargs(cfg))

    if backend == "redis":
        from ..integrations.redis import RedisAgentMemory

        return RedisAgentMemory(
            cfg.url or "redis://localhost:6379/0",
            **_common_kwargs(cfg),
            **cfg.options,
        )

    raise ValueError(f"Unsupported memory backend: {backend!r}")


def create_stores(
    config: Any = None,
) -> Tuple[ConversationStore, AgentMemory]:
    """Create both the conversation store and agent memory from one config."""
    cfg = _coerce_config(config)
    return create_conversation_store(cfg), create_agent_memory(cfg)


def apply_storage_config(agent: Any, config: Any = None) -> Any:
    """Apply a storage config to an existing Agent in place and return it.

    用于服务端/CLI：Agent 已由示例工厂创建，这里按配置替换其会话存储与记忆后端。
    """
    conversation_store, agent_memory = create_stores(config)
    agent.conversation_store = conversation_store
    agent.agent_memory = agent_memory
    return agent
