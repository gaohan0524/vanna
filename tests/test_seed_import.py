"""T3-6 idempotent seed import tests."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import List

from analytics_agent.memory.backends.chroma_local import ChromaLocalMemory
from analytics_agent.memory.seeding import SeedImporter


class _TestEmbedding:
    """Offline deterministic embedding; only tests import/upsert behavior."""

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    def name(self) -> str:
        return "seed-import-test"

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self.embed_documents(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        vectors = []
        for text in input:
            vector = [0.0] * self.dimension
            for char in text:
                bucket = hashlib.sha256(char.encode("utf-8")).digest()[0]
                vector[bucket % self.dimension] += 1.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    def embed_query(self, input: List[str]) -> List[List[float]]:
        return self.embed_documents(input)


def _memory(tmp_path: Path) -> ChromaLocalMemory:
    return ChromaLocalMemory(
        persist_directory=str(tmp_path),
        collection_name="seed_import",
        embedding_function=_TestEmbedding(),
    )


def test_seed_import_is_idempotent(tmp_path):
    memory = _memory(tmp_path)
    importer = SeedImporter(
        memory,
        seed_dir=Path("analytics_agent/memory/seeds"),
        manifest_path=None,
        prune=True,
    )

    first_plan = importer.plan()
    assert first_plan.creates == first_plan.total
    assert first_plan.updates == 0
    asyncio.run(importer.apply(first_plan))
    first_count = memory.collection.count()

    second_plan = importer.plan()
    assert second_plan.creates == 0
    assert second_plan.updates == 0
    assert second_plan.skips == second_plan.total
    asyncio.run(importer.apply(second_plan))

    assert memory.collection.count() == first_count


def test_seed_import_can_list_and_dry_run_rollback(tmp_path):
    memory = _memory(tmp_path)
    importer = SeedImporter(
        memory,
        seed_dir=Path("analytics_agent/memory/seeds"),
        manifest_path=None,
        prune=True,
    )
    plan = importer.plan()
    asyncio.run(importer.apply(plan))

    records = importer.list_records(source="v1_30.yaml#B-02")
    assert records
    assert all(record["seed_source"] == "v1_30.yaml#B-02" for record in records)

    count = asyncio.run(importer.delete_records(source="v1_30.yaml#B-02", dry_run=True))
    assert count == len(records)
    assert memory.collection.count() == len(plan.records)
