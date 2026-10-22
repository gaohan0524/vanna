"""Unit tests for the .env loader (no external dependency, no network)."""

from __future__ import annotations

import os

from analytics_agent.config import load_dotenv


def test_load_dotenv_parses_pairs_and_quotes(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment line",
                "FOO=bar",
                'QUOTED="x y"',
                "EMPTY=",
                "PASSWORD=with#hash",
            ]
        ),
        encoding="utf-8",
    )

    for key in ("FOO", "QUOTED", "EMPTY", "PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    load_dotenv(env_file)

    assert os.environ["FOO"] == "bar"
    assert os.environ["QUOTED"] == "x y"
    assert os.environ["EMPTY"] == ""
    # 密码里的 # 不应被当成注释
    assert os.environ["PASSWORD"] == "with#hash"


def test_load_dotenv_skips_missing_file(tmp_path):
    load_dotenv(tmp_path / "does-not-exist.env")  # 不应抛异常


def test_existing_env_wins(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "from-process")

    load_dotenv(env_file)

    assert os.environ["FOO"] == "from-process"
