"""Stage 3 种子幂等导入。

设计目标：

- 文本：一条文本 = 一条向量，按 ``seed_id + seed_hash`` 记录；
- 工具：一个 SQL 模板展开为 question + paraphrases，多问法共享 ``sql_hash``；
- 相同内容重复执行不新增记录；
- 旧问法/旧版本在 ``--prune`` 下删除，手工写入的非种子记忆不受影响；
- 默认 dry-run，只有 ``--apply`` 才写入 Chroma。

运行：
    python -m analytics_agent.memory.seeding --dry-run
    python -m analytics_agent.memory.seeding --apply

默认使用 ``Settings.from_env()`` 的 MEMORY_PATH / MEMORY_COLLECTION /
MEMORY_EMBEDDING_MODEL，和 Web/Agent 完全一致。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import sqlglot
import yaml
from vanna import User
from vanna.core.tool import ToolContext

from analytics_agent.config import Settings

from .backends.chroma_local import ChromaLocalMemory
from .embedding import ChineseEmbeddingFunction

SEED_DIR = Path("analytics_agent/memory/seeds")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def normalized_sql(sql: str) -> str:
    """去掉尾分号、压缩空白并统一 SQL 方言输出，作为工具记忆去重键。"""
    try:
        expression = sqlglot.parse_one(sql, read="clickhouse")
        normalized = expression.sql(dialect="clickhouse", pretty=False)
    except Exception:
        normalized = re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()
    return normalized.rstrip(";")


def sql_hash(sql: str) -> str:
    return _sha256(normalized_sql(sql))


def _load_yaml(path: Path) -> List[Dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} 必须是 YAML 列表")
    return data


def _validate_seed_documents(
    knowledge: Sequence[Dict[str, Any]],
    tools: Sequence[Dict[str, Any]],
) -> None:
    ids: List[str] = []
    for item in knowledge:
        required = {"id", "text", "tags", "source", "version", "reviewed"}
        missing = required - set(item)
        if missing:
            raise ValueError(f"文本种子 {item.get('id')} 缺字段: {sorted(missing)}")
        if not item["reviewed"]:
            raise ValueError(f"文本种子未审核: {item['id']}")
        if len(item["text"]) > 200:
            raise ValueError(f"文本种子超过 200 字: {item['id']}")
        ids.append(item["id"])

    expected_cases = set()
    for item in tools:
        required = {
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
        missing = required - set(item)
        if missing:
            raise ValueError(f"工具种子 {item.get('id')} 缺字段: {sorted(missing)}")
        if not item["reviewed"]:
            raise ValueError(f"工具种子未审核: {item['id']}")
        if not 3 <= len(item["paraphrases"]) <= 5:
            raise ValueError(f"工具种子问法数不在 3~5: {item['id']}")
        if item["tool_name"] != "run_sql":
            raise ValueError(f"暂只支持 run_sql: {item['id']}")
        sqlglot.parse_one(item["sql"], read="clickhouse")
        expected_cases.update(item.get("covers", [item["source"].split("#", 1)[1]]))
        ids.append(item["id"])

    if len(ids) != len(set(ids)):
        raise ValueError("种子 ID 存在重复")

    # 覆盖 22 条 answer；允许一个模板 covers 多条用例。
    dataset = yaml.safe_load(
        Path("analytics_agent/evaluation/datasets/v1_30.yaml").read_text(
            encoding="utf-8"
        )
    )
    answer_ids = {
        case["id"]
        for case in dataset["dataset"]["test_cases"]
        if case["metadata"].get("expected_behavior", "answer") == "answer"
    }
    if expected_cases != answer_ids:
        raise ValueError(
            f"工具种子未覆盖全部 answer: missing={answer_ids - expected_cases}, "
            f"extra={expected_cases - answer_ids}"
        )


@dataclass
class PlannedRecord:
    kind: str
    memory_id: str
    seed_id: str
    seed_hash: str
    question: str
    document: str
    metadata: Dict[str, Any]
    sql: Optional[str] = None
    action: str = "create"

    @property
    def is_tool(self) -> bool:
        return self.kind == "tool"


@dataclass
class ImportPlan:
    records: List[PlannedRecord]
    creates: int
    updates: int
    skips: int

    @property
    def total(self) -> int:
        return len(self.records)


class SeedImporter:
    """读取 YAML、计算计划并写入 Chroma。"""

    def __init__(
        self,
        memory: ChromaLocalMemory,
        *,
        seed_dir: Path = SEED_DIR,
        manifest_path: Optional[Path] = None,
        prune: bool = True,
    ) -> None:
        self.memory = memory
        self.seed_dir = seed_dir
        self.manifest_path = manifest_path or seed_dir / "manifest.yaml"
        self.prune = prune
        self.context = self._seed_context()

    def _seed_context(self) -> ToolContext:
        """种子统一写入 shared scope，不归属具体用户。"""
        user = User(
            id="seed_import",
            username="seed_import",
            group_memberships=["admin"],
            metadata={"tenant_id": "seed"},
        )
        return ToolContext(
            user=user,
            conversation_id="seed_import",
            request_id="seed_import",
            agent_memory=self.memory,
        )

    def load(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        knowledge = _load_yaml(self.seed_dir / "knowledge.yaml")
        tools = _load_yaml(self.seed_dir / "tool_memories.yaml")
        _validate_seed_documents(knowledge, tools)
        return knowledge, tools

    def _add_text(self, records: List[PlannedRecord], item: Dict[str, Any]) -> None:
        content = item["text"]
        seed_hash = _sha256(content)
        metadata = {
            "scope": "shared",
            "seed_id": item["id"],
            "seed_source": item["source"],
            "seed_version": item["version"],
            "seed_hash": seed_hash,
            "seed_kind": "text",
            "reviewed": True,
            "tags": item.get("tags", []),
        }
        memory_id = self.memory.text_memory_id(content, self.context, metadata)
        records.append(
            PlannedRecord(
                kind="text",
                memory_id=memory_id,
                seed_id=item["id"],
                seed_hash=seed_hash,
                question="",
                document=content,
                metadata=metadata,
            )
        )

    def _add_tool(self, records: List[PlannedRecord], item: Dict[str, Any]) -> None:
        sql = item["sql"].strip().rstrip(";")
        seed_hash = sql_hash(sql)
        variants = [item["question"], *item["paraphrases"]]
        # 去重但保留顺序；同一 YAML 里重复问法只建一条。
        variants = list(dict.fromkeys(variants))
        for index, question in enumerate(variants):
            metadata = {
                "scope": "shared",
                "seed_id": item["id"],
                "seed_source": item["source"],
                "seed_version": item["version"],
                "seed_hash": seed_hash,
                "seed_kind": "tool",
                "reviewed": True,
                "sql_hash": seed_hash,
                "variant_index": index,
                "covers": item.get("covers", [item["source"].split("#", 1)[1]]),
            }
            args = {"sql": sql}
            memory_id = self.memory.tool_memory_id(
                question, item["tool_name"], args, self.context, metadata
            )
            records.append(
                PlannedRecord(
                    kind="tool",
                    memory_id=memory_id,
                    seed_id=item["id"],
                    seed_hash=seed_hash,
                    question=question,
                    document=question,
                    metadata=metadata,
                    sql=sql,
                )
            )

    def _existing(self, records: List[PlannedRecord]) -> Dict[str, Dict[str, Any]]:
        if not records:
            return {}
        result = self.memory.collection.get(
            ids=[record.memory_id for record in records],
            include=["metadatas"],
        )
        return {
            memory_id: metadata
            for memory_id, metadata in zip(
                result.get("ids") or [], result.get("metadatas") or []
            )
            if metadata
        }

    def plan(self) -> ImportPlan:
        knowledge, tools = self.load()
        records: List[PlannedRecord] = []
        for item in knowledge:
            self._add_text(records, item)
        for item in tools:
            self._add_tool(records, item)

        ids = [record.memory_id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("展开后的记忆 ID 存在重复，请检查种子内容")

        existing = self._existing(records)
        creates = updates = skips = 0
        for record in records:
            current = existing.get(record.memory_id)
            if current is None:
                record.action = "create"
                creates += 1
            elif (
                current.get("seed_id") == record.seed_id
                and current.get("seed_hash") == record.seed_hash
                and int(current.get("seed_version", -1))
                == int(record.metadata["seed_version"])
                and bool(current.get("reviewed"))
            ):
                record.action = "skip"
                skips += 1
            else:
                record.action = "update"
                updates += 1

        return ImportPlan(
            records=records, creates=creates, updates=updates, skips=skips
        )

    async def apply(self, plan: ImportPlan) -> ImportPlan:
        """按计划写入；只处理 create/update，skip 不动。"""
        for record in plan.records:
            if record.action == "skip":
                continue
            if record.is_tool:
                await self.memory.save_tool_usage(
                    record.question,
                    "run_sql",
                    {"sql": record.sql or ""},
                    self.context,
                    success=True,
                    metadata=record.metadata,
                )
            else:
                await self.memory.save_text_memory(
                    record.document,
                    self.context,
                    metadata=record.metadata,
                )

        if self.prune:
            await self._prune(plan)

        self._write_manifest(plan)
        return plan

    def list_records(
        self,
        *,
        source: Optional[str] = None,
        version: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """列出种子记录，可按 source/version 过滤，供审核和回滚前确认。"""
        rows = self.memory.collection.get(include=["metadatas"])
        output: List[Dict[str, Any]] = []
        for memory_id, metadata in zip(
            rows.get("ids") or [], rows.get("metadatas") or []
        ):
            if not metadata or not metadata.get("seed_id"):
                continue
            if source and metadata.get("seed_source") != source:
                continue
            if version is not None and int(metadata.get("seed_version", -1)) != version:
                continue
            output.append(
                {
                    "memory_id": memory_id,
                    "seed_id": metadata.get("seed_id"),
                    "seed_source": metadata.get("seed_source"),
                    "seed_version": metadata.get("seed_version"),
                    "kind": metadata.get("kind"),
                }
            )
        return sorted(output, key=lambda item: (item["seed_id"], item["memory_id"]))

    async def delete_records(
        self,
        *,
        source: str,
        version: Optional[int] = None,
        dry_run: bool = True,
    ) -> int:
        """按 source/version 删除种子记录；默认只统计，不真正删除。"""
        records = self.list_records(source=source, version=version)
        if dry_run or not records:
            return len(records)
        ids = [record["memory_id"] for record in records]
        await asyncio.to_thread(self.memory.collection.delete, ids=ids)
        return len(ids)

    async def _prune(self, plan: ImportPlan) -> int:
        """删除已不在当前种子中的旧版本/旧问法；不碰无 seed_id 的手工记忆。"""
        expected_by_kind = {
            "text": {r.memory_id for r in plan.records if r.kind == "text"},
            "tool": {r.memory_id for r in plan.records if r.kind == "tool"},
        }
        deleted = 0
        for kind, expected in expected_by_kind.items():
            rows = self.memory.collection.get(
                where={"seed_kind": kind},
                include=["metadatas"],
            )
            stale = [
                memory_id
                for memory_id, metadata in zip(
                    rows.get("ids") or [], rows.get("metadatas") or []
                )
                if metadata and metadata.get("seed_id") and memory_id not in expected
            ]
            if stale:
                await asyncio.to_thread(self.memory.collection.delete, ids=stale)
                deleted += len(stale)
        return deleted

    def _write_manifest(self, plan: ImportPlan) -> None:
        if not self.manifest_path:
            return
        data = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        text_seed_ids = {r.seed_id for r in plan.records if r.kind == "text"}
        tool_seed_ids = {r.seed_id for r in plan.records if r.kind == "tool"}
        data.update(
            {
                "status": "imported",
                "imported_at": datetime.now().isoformat(timespec="seconds"),
                "knowledge_count": len(text_seed_ids),
                "tool_memory_count": len(tool_seed_ids),
                "reviewed_count": len(text_seed_ids | tool_seed_ids),
                "vector_count": len(plan.records),
            }
        )
        self.manifest_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def _build_memory(settings: Settings) -> ChromaLocalMemory:
    embedding = ChineseEmbeddingFunction(settings.memory_embedding_model)
    return ChromaLocalMemory(
        persist_directory=str(settings.memory_path),
        collection_name=settings.memory_collection,
        embedding_function=embedding,
    )


def _print_plan(plan: ImportPlan) -> None:
    print(
        f"plan: total={plan.total} create={plan.creates} "
        f"update={plan.updates} skip={plan.skips}"
    )
    by_kind: Dict[str, Dict[str, int]] = {}
    for record in plan.records:
        bucket = by_kind.setdefault(record.kind, {"create": 0, "update": 0, "skip": 0})
        bucket[record.action] += 1
    for kind, counts in sorted(by_kind.items()):
        print(
            f"  {kind}: create={counts['create']} update={counts['update']} "
            f"skip={counts['skip']}"
        )


async def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if args.path:
        settings = Settings(**{**settings.__dict__, "memory_path": Path(args.path)})
    if args.collection:
        settings = Settings(
            **{**settings.__dict__, "memory_collection": args.collection}
        )
    memory = _build_memory(settings)
    importer = SeedImporter(
        memory,
        seed_dir=Path(args.seeds_dir),
        prune=not args.no_prune,
    )
    if args.list:
        records = importer.list_records(source=args.source, version=args.version)
        print(f"records: {len(records)}")
        for record in records:
            print(
                f"  {record['kind']:<4} {record['seed_version']} "
                f"{record['seed_source']} {record['seed_id']} "
                f"{record['memory_id']}"
            )
        return 0
    if args.delete_source:
        count = await importer.delete_records(
            source=args.delete_source,
            version=args.version,
            dry_run=args.dry_run,
        )
        action = "would delete" if args.dry_run else "deleted"
        print(f"{action}: {count} record(s), source={args.delete_source}")
        return 0
    plan = importer.plan()
    _print_plan(plan)
    if args.dry_run:
        print("dry-run：未写入 Chroma")
        return 0
    await importer.apply(plan)
    print("imported")
    print(f"collection: {settings.memory_collection}")
    print(f"vectors: {len(plan.records)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T3-6 seed importer")
    parser.add_argument("--seeds-dir", default=str(SEED_DIR))
    parser.add_argument("--path", help="Chroma directory override")
    parser.add_argument("--collection", help="Chroma collection override")
    parser.add_argument("--list", action="store_true", help="list seed-tagged records")
    parser.add_argument("--source", help="source filter, e.g. v1_30.yaml#B-02")
    parser.add_argument("--version", type=int, help="seed version filter")
    parser.add_argument(
        "--delete-source",
        help="delete seed-tagged records with this source (requires --apply)",
    )
    parser.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="write to Chroma (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="only print the plan (default)",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="keep stale seed-tagged records instead of deleting them",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
