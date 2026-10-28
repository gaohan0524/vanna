"""Runtime configuration loaded from environment variables.

所有敏感信息（数据库口令、API Key）只从环境变量读取，禁止写进源码。
`.env.example` 提供了完整变量清单。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Load ``KEY=VALUE`` pairs from a .env file without extra dependencies.

    已存在的环境变量优先（便于 CI / 临时覆盖）。文件不存在时静默跳过。
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_DOTENV_LOADED = False


def ensure_dotenv() -> None:
    """Load `<repo>/.env` once per process (no-op when absent)."""
    global _DOTENV_LOADED
    if not _DOTENV_LOADED:
        load_dotenv(PROJECT_ROOT / ".env")
        _DOTENV_LOADED = True


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    """Connection and runtime settings.

    Attributes:
        ch_host: ClickHouse HTTP 地址。
        ch_port: ClickHouse HTTP 端口（默认 8123）。
        ch_database: 评测库名。
        ch_user / ch_password: 查询账号，生产上必须是只读账号。
        ch_write_user / ch_write_password: 建表与灌数账号，仅 seed 使用。
        ch_timeout_s: 单条 SQL 超时（秒），同时作为 `max_execution_time`。
        ch_max_rows: 单条 SQL 返回行数上限，防止评测打满内存。
        llm_provider: `ollama` / `deepseek` / `openai` / `anthropic`。
        llm_model: 模型名（DeepSeek 用 `deepseek-chat`）。
        ollama_host: Ollama 服务地址。
        llm_timeout_s / llm_max_predict: 单次调用超时与生成上限。
        agent_max_iterations: 单轮最多工具调用次数。
        deepseek_api_key / deepseek_base_url: DeepSeek OpenAI 兼容端点配置。
        memory_backend: `demo`（默认）或 `chroma_local`。
        memory_path: 本地 Chroma 目录；不要提交到 git。
        memory_collection: Chroma collection 名。
        memory_embedding_model: 中文 embedding 模型名，必须与 collection 一致。
    """

    ch_host: str = "localhost"
    ch_port: int = 8123
    ch_database: str = "shop"
    ch_user: str = "readonly"
    ch_password: str = ""
    ch_write_user: str = ""
    ch_write_password: str = ""
    ch_timeout_s: int = 30
    ch_max_rows: int = 10_000

    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    ollama_host: str = "http://localhost:11434"
    llm_num_ctx: int = 8192
    llm_timeout_s: int = 600
    llm_max_predict: int = 512
    agent_max_iterations: int = 8

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    memory_backend: str = "demo"
    memory_path: Path = PROJECT_ROOT / "analytics_agent/memory/data/chroma"
    memory_collection: str = "analytics_memory_v1"
    memory_embedding_model: str = "BAAI/bge-small-zh-v1.5"

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables (with safe defaults)."""
        ensure_dotenv()
        user = _env("CH_USER", "readonly")
        password = _env("CH_PASSWORD")
        return cls(
            ch_host=_env("CH_HOST", "localhost"),
            ch_port=_env_int("CH_PORT", 8123),
            ch_database=_env("CH_DATABASE", "shop"),
            ch_user=user,
            ch_password=password,
            ch_write_user=_env("CH_WRITE_USER", user),
            ch_write_password=_env("CH_WRITE_PASSWORD", password),
            ch_timeout_s=_env_int("CH_TIMEOUT_S", 30),
            ch_max_rows=_env_int("CH_MAX_ROWS", 10_000),
            llm_provider=_env("LLM_PROVIDER", "ollama").lower(),
            llm_model=_env("LLM_MODEL", "qwen2.5:7b"),
            ollama_host=_env("OLLAMA_HOST", "http://localhost:11434"),
            llm_num_ctx=_env_int("LLM_NUM_CTX", 8192),
            llm_timeout_s=_env_int("LLM_TIMEOUT_S", 600),
            llm_max_predict=_env_int("LLM_MAX_PREDICT", 512),
            agent_max_iterations=_env_int("AGENT_MAX_ITERATIONS", 5),
            deepseek_api_key=_env("DEEPSEEK_API_KEY"),
            deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            memory_backend=_env("MEMORY_BACKEND", "demo").lower(),
            memory_path=Path(
                _env(
                    "MEMORY_PATH",
                    str(PROJECT_ROOT / "analytics_agent/memory/data/chroma"),
                )
            ),
            memory_collection=_env("MEMORY_COLLECTION", "analytics_memory_v1"),
            memory_embedding_model=_env(
                "MEMORY_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
            ),
        )

    @property
    def reports_dir(self) -> Path:
        """评测报告输出目录（默认 `<repo>/reports`）。"""
        raw = _env("EVAL_REPORTS_DIR")
        return Path(raw) if raw else PROJECT_ROOT / "reports"

    @property
    def schema_catalog_path(self) -> Path:
        """Stage 1 schema catalog（可用 SCHEMA_CATALOG_PATH 覆盖）。"""
        raw = _env("SCHEMA_CATALOG_PATH")
        return (
            Path(raw)
            if raw
            else PROJECT_ROOT / "analytics_agent/context/schema_catalog.json"
        )

    @property
    def glossary_path(self) -> Path:
        """Stage 1 指标术语表（可用 GLOSSARY_PATH 覆盖）。"""
        raw = _env("GLOSSARY_PATH")
        return (
            Path(raw) if raw else PROJECT_ROOT / "analytics_agent/context/glossary.yaml"
        )

    @property
    def datasets_dir(self) -> Path:
        """评测用例目录。"""
        return Path(__file__).resolve().parent / "evaluation" / "datasets"

    @property
    def seed_dir(self) -> Path:
        return Path(__file__).resolve().parent / "evaluation" / "seed"

    def admin_credentials(self) -> tuple[str, str, Optional[str]]:
        """Return (user, password, database) for seeding.

        Seeding connects without a database because `CREATE DATABASE shop`
        必须在该库存在之前执行。
        """
        user = self.ch_write_user or self.ch_user
        password = self.ch_write_password or self.ch_password
        return user, password, None
