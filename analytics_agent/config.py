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
        llm_provider: `ollama` / `openai` / `anthropic`。
        llm_model: 模型名。
        ollama_host: Ollama 服务地址。
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

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables (with safe defaults)."""
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
        )

    @property
    def reports_dir(self) -> Path:
        """评测报告输出目录（默认 `<repo>/reports`）。"""
        raw = _env("EVAL_REPORTS_DIR")
        return Path(raw) if raw else PROJECT_ROOT / "reports"

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
