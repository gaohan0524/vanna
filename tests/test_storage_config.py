"""
Storage configuration & backend factory tests.

验证 StorageConfig 的字典/环境变量解析、后端校验与工厂分发；SQLite 走完整往返；
Postgres/MySQL/Redis 需要外部服务，提供可通过环境变量启用的集成测试（默认跳过）。
"""

import os
from types import SimpleNamespace

import pytest

from vanna.config import (
    StorageConfig,
    apply_storage_config,
    create_conversation_store,
    create_agent_memory,
    create_stores,
)
from vanna.config.storage import _mysql_params
from vanna.core import User
from vanna.integrations.local import MemoryConversationStore
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.sqlite import (
    SQLiteAgentMemory,
    SQLiteConversationStore,
)


# --------------------------------------------------------------------------- #
# StorageConfig parsing / validation
# --------------------------------------------------------------------------- #
def test_from_dict_basic():
    cfg = StorageConfig.from_dict(
        {"backend": "sqlite", "sqlite_path": "/tmp/x.db", "tenant_sharing": True}
    )
    assert cfg.backend == "sqlite"
    assert cfg.sqlite_path == "/tmp/x.db"
    assert cfg.tenant_sharing is True


def test_from_dict_collects_unknown_keys_into_options():
    cfg = StorageConfig.from_dict(
        {"backend": "postgres", "url": "postgresql://h/db", "sslmode": "require"}
    )
    assert cfg.options == {"sslmode": "require"}


def test_from_dict_separate_backends():
    cfg = StorageConfig.from_dict(
        {
            "backend": "memory",
            "conversation_backend": "sqlite",
            "memory_backend": "redis",
        }
    )
    assert cfg.resolved_conversation_backend() == "sqlite"
    assert cfg.resolved_memory_backend() == "redis"


def test_from_env_parsing():
    cfg = StorageConfig.from_env(
        environ={
            "VANNA_STORAGE_BACKEND": "redis",
            "VANNA_STORAGE_URL": "redis://localhost:6379/0",
            "VANNA_STORAGE_TENANT_SHARING": "true",
        }
    )
    assert cfg.backend == "redis"
    assert cfg.url == "redis://localhost:6379/0"
    assert cfg.tenant_sharing is True


def test_validate_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unsupported"):
        StorageConfig(backend="mongodb").validate()


def test_backend_inferred_from_url_when_default():
    cfg = StorageConfig(url="postgresql://host/db")
    assert cfg.resolved_conversation_backend() == "postgres"
    assert cfg.resolved_memory_backend() == "postgres"

    redis_cfg = StorageConfig(url="redis://localhost:6379/0")
    assert redis_cfg.resolved_conversation_backend() == "redis"


def test_explicit_backend_not_overridden_by_url():
    cfg = StorageConfig(backend="sqlite", url="postgresql://host/db")
    assert cfg.resolved_conversation_backend() == "sqlite"


def test_mysql_url_parsing():
    params = _mysql_params(StorageConfig(url="mysql://root:secret@db:3307/vanna"))
    assert params == {
        "host": "db",
        "port": 3307,
        "user": "root",
        "password": "secret",
        "database": "vanna",
    }


# --------------------------------------------------------------------------- #
# Factory dispatch
# --------------------------------------------------------------------------- #
def test_factory_memory_backend():
    conv, mem = create_stores(StorageConfig(backend="memory"))
    assert isinstance(conv, MemoryConversationStore)
    assert isinstance(mem, DemoAgentMemory)
    # 配置驱动的内存后端默认开启用户隔离
    assert mem._user_scoped is True


def test_factory_sqlite_backend_roundtrip(tmp_path):
    cfg = StorageConfig(backend="sqlite", sqlite_path=str(tmp_path / "vanna.db"))
    conv, mem = create_stores(cfg)
    assert isinstance(conv, SQLiteConversationStore)
    assert isinstance(mem, SQLiteAgentMemory)
    conv.close()
    mem.close()


async def test_apply_storage_config_sets_agent_stores(tmp_path):
    agent = SimpleNamespace(conversation_store=None, agent_memory=None)
    apply_storage_config(
        agent, {"backend": "sqlite", "sqlite_path": str(tmp_path / "vanna.db")}
    )
    assert isinstance(agent.conversation_store, SQLiteConversationStore)
    assert isinstance(agent.agent_memory, SQLiteAgentMemory)
    agent.conversation_store.close()
    agent.agent_memory.close()


def test_create_conversation_store_and_memory_independently():
    conv = create_conversation_store({"backend": "memory"})
    mem = create_agent_memory({"backend": "memory"})
    assert isinstance(conv, MemoryConversationStore)
    assert isinstance(mem, DemoAgentMemory)


# --------------------------------------------------------------------------- #
# External backends (opt-in integration tests)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.getenv("VANNA_TEST_POSTGRES_URL"), reason="VANNA_TEST_POSTGRES_URL not set"
)
async def test_postgres_roundtrip_and_isolation():
    url = os.environ["VANNA_TEST_POSTGRES_URL"]
    conv, mem = create_stores(StorageConfig(backend="postgres", url=url))
    alice, bob = User(id="alice"), User(id="bob")
    try:
        await conv.create_conversation("pg-c1", alice, "hello")
        assert await conv.get_conversation("pg-c1", alice) is not None
        assert await conv.get_conversation("pg-c1", bob) is None
    finally:
        await conv.delete_conversation("pg-c1", alice)
    assert conv is not None and mem is not None


@pytest.mark.skipif(
    not os.getenv("VANNA_TEST_MYSQL_URL"), reason="VANNA_TEST_MYSQL_URL not set"
)
async def test_mysql_roundtrip_and_isolation():
    cfg = StorageConfig(backend="mysql", url=os.environ["VANNA_TEST_MYSQL_URL"])
    conv, mem = create_stores(cfg)
    alice, bob = User(id="alice"), User(id="bob")
    try:
        await conv.create_conversation("my-c1", alice, "hello")
        assert await conv.get_conversation("my-c1", alice) is not None
        assert await conv.get_conversation("my-c1", bob) is None
    finally:
        await conv.delete_conversation("my-c1", alice)
    assert conv is not None and mem is not None


@pytest.mark.skipif(
    not os.getenv("VANNA_TEST_REDIS_URL"), reason="VANNA_TEST_REDIS_URL not set"
)
async def test_redis_roundtrip_and_isolation():
    url = os.environ["VANNA_TEST_REDIS_URL"]
    conv, mem = create_stores(StorageConfig(backend="redis", url=url))
    alice, bob = User(id="alice"), User(id="bob")
    try:
        await conv.create_conversation("rd-c1", alice, "hello")
        loaded = await conv.get_conversation("rd-c1", alice)
        assert loaded is not None and loaded.messages[0].content == "hello"
        assert await conv.get_conversation("rd-c1", bob) is None
    finally:
        await conv.delete_conversation("rd-c1", alice)
        await conv.close()
        await mem.close()
