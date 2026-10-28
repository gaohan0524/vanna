"""Static validation for Stage 3 seed drafts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import sqlglot
import yaml

SEED_DIR = Path("analytics_agent/memory/seeds")
DATASET = Path("analytics_agent/evaluation/datasets/v1_30.yaml")


def _load(name: str):
    return yaml.safe_load((SEED_DIR / name).read_text(encoding="utf-8"))


def _sql_hash(sql: str) -> str:
    normalized = re.sub(r"\s+", " ", sql).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_seed_counts_and_unique_ids():
    knowledge = _load("knowledge.yaml")
    tools = _load("tool_memories.yaml")

    assert 30 <= len(knowledge) <= 50
    assert len(tools) == 22

    ids = [item["id"] for item in knowledge] + [item["id"] for item in tools]
    assert len(ids) == len(set(ids))
    assert all(" " not in item_id for item_id in ids)


def test_text_memories_are_reviewable_atomic_facts():
    knowledge = _load("knowledge.yaml")

    for item in knowledge:
        assert set(item) >= {"id", "text", "tags", "source", "version", "reviewed"}
        assert len(item["text"]) <= 200
        assert item["tags"]
        assert item["source"]
        assert item["version"] >= 1
        assert isinstance(item["reviewed"], bool)


def test_tool_memories_cover_all_answer_cases():
    tools = _load("tool_memories.yaml")
    dataset = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    answer_ids = {
        case["id"]
        for case in dataset["dataset"]["test_cases"]
        if case["metadata"].get("expected_behavior", "answer") == "answer"
    }

    source_ids = {
        item["source"].split("#", 1)[1]
        for item in tools
        if item["source"].startswith("v1_30.yaml#")
    }
    assert source_ids == answer_ids


def test_tool_memories_have_paraphrases_and_parseable_sql():
    tools = _load("tool_memories.yaml")
    by_id = {item["id"]: item for item in tools}
    dataset = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in dataset["dataset"]["test_cases"]}

    for item in tools:
        assert set(item) >= {
            "id",
            "question",
            "paraphrases",
            "tool_name",
            "sql",
            "tables",
            "metrics",
            "source",
            "version",
            "reviewed",
        }
        assert 3 <= len(item["paraphrases"]) <= 5
        assert item["tool_name"] == "run_sql"
        assert item["tables"]
        assert item["metrics"]
        assert item["source"]
        assert item["version"] >= 1
        assert isinstance(item["reviewed"], bool)
        assert len(sqlglot.parse(item["sql"], read="clickhouse")) == 1

        source_case = cases[item["source"].split("#", 1)[1]]
        golden = source_case["metadata"]["golden_sql"].strip().rstrip(";")
        assert item["sql"].strip() == golden

        canonical_id = item.get("canonical_id")
        if canonical_id:
            assert canonical_id in by_id
            assert _sql_hash(item["sql"]) == _sql_hash(by_id[canonical_id]["sql"])
