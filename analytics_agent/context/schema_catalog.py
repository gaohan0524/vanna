"""Build a compact schema catalog from ClickHouse system tables.

用法：
    python -m analytics_agent.context.schema_catalog refresh
    python -m analytics_agent.context.schema_catalog refresh --out path/to.json

设计：
- 表/字段注释优先取数据库 ``COMMENT``，``annotations.yaml`` 只做可选补充；
- 只对 ``LowCardinality`` 列采样枚举，避免全表扫描；
- 行数存进 catalog（供 Stage 2 工具用），但**不渲染**进提示词。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import clickhouse_connect
import yaml

from analytics_agent.config import PROJECT_ROOT, Settings

CATALOG_VERSION = 1
ENUM_SAMPLE_LIMIT = 20
DEFAULT_OUT = PROJECT_ROOT / "analytics_agent/context/schema_catalog.json"
DEFAULT_ANNOTATIONS = PROJECT_ROOT / "analytics_agent/context/annotations.yaml"

# 提示词里的展示顺序：按业务重要性，不按字母序
TABLE_ORDER = (
    "orders",
    "order_items",
    "payments",
    "refunds",
    "users",
    "products",
    "sessions",
    "events",
    "accounts",
    "campaigns",
    "dim_date",
    "meta_data_anchor",
)


def _connect(settings: Settings):
    user, password, database = settings.admin_credentials()
    return clickhouse_connect.get_client(
        host=settings.ch_host,
        port=settings.ch_port,
        username=user,
        password=password,
        database=database,
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _table_key(name: str) -> tuple[int, str]:
    try:
        return (TABLE_ORDER.index(name), name)
    except ValueError:
        return (len(TABLE_ORDER), name)


def load_annotations(path: Path | str = DEFAULT_ANNOTATIONS) -> Dict[str, Any]:
    """Load optional per-table/per-column notes. Missing file means no notes."""
    path = Path(path)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tables = data.get("tables") or {}
    if not isinstance(tables, dict):
        raise ValueError(f"{path}: 'tables' must be a mapping")
    return tables


def _enum_values(client, database: str, table: str, column: str) -> Optional[List[str]]:
    rows = client.query(
        f"SELECT {_quote(column)}, count() AS c "
        f"FROM {database}.{_quote(table)} "
        f"GROUP BY {_quote(column)} "
        f"ORDER BY c DESC LIMIT {ENUM_SAMPLE_LIMIT + 1}"
    ).result_rows
    values = [row[0] for row in rows if row[0] is not None]
    if len(values) > ENUM_SAMPLE_LIMIT:
        return None
    return values or None


def extract(
    settings: Optional[Settings] = None,
    annotations_path: Path | str = DEFAULT_ANNOTATIONS,
) -> Dict[str, Any]:
    """Read system tables and return the catalog dict (no file IO)."""
    s = settings or Settings.from_env()
    database = s.ch_database
    notes = load_annotations(annotations_path)

    client = _connect(s)
    try:
        table_rows = client.query(
            "SELECT name, engine, comment FROM system.tables "
            f"WHERE database = '{_escape(database)}'"
        ).result_rows
        column_rows = client.query(
            "SELECT table, name, type, comment FROM system.columns "
            f"WHERE database = '{_escape(database)}' "
            "ORDER BY table, position"
        ).result_rows

        tables_meta = {
            name: {"engine": engine or "", "comment": comment or ""}
            for name, engine, comment in table_rows
        }
        columns: Dict[str, List[Dict[str, Any]]] = {}
        for table, name, type_name, comment in column_rows:
            columns.setdefault(table, []).append(
                {"name": name, "type": type_name, "comment": comment or ""}
            )

        tables: List[Dict[str, Any]] = []
        for table_name in sorted(tables_meta, key=_table_key):
            table_notes = notes.get(table_name) or {}
            column_notes = table_notes.get("columns") or {}
            rendered_columns: List[Dict[str, Any]] = []
            for column in columns.get(table_name, []):
                enum = (
                    _enum_values(client, database, table_name, column["name"])
                    if "LowCardinality" in column["type"]
                    else None
                )
                rendered_columns.append(
                    {
                        "name": column["name"],
                        "type": column["type"],
                        "nullable": column["type"].startswith("Nullable("),
                        "comment": column["comment"]
                        or column_notes.get(column["name"], ""),
                        "enum": enum,
                    }
                )

            rows = client.query(
                f"SELECT count() FROM {database}.{_quote(table_name)}"
            ).result_rows[0][0]
            tables.append(
                {
                    "name": table_name,
                    "engine": tables_meta[table_name]["engine"],
                    "rows": int(rows),
                    "summary": tables_meta[table_name]["comment"]
                    or table_notes.get("summary", ""),
                    "columns": rendered_columns,
                }
            )
    finally:
        client.close()

    return {
        "version": CATALOG_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "database": database,
        "tables": tables,
    }


def write_catalog(catalog: Dict[str, Any], path: Path | str = DEFAULT_OUT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def refresh(
    settings: Optional[Settings] = None,
    *,
    out: Path | str = DEFAULT_OUT,
    annotations: Path | str = DEFAULT_ANNOTATIONS,
) -> Path:
    catalog = extract(settings, annotations)
    return write_catalog(catalog, out)


def load_catalog(path: Path | str = DEFAULT_OUT) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"schema catalog not found: {path}\n"
            "run: python -m analytics_agent.context.schema_catalog refresh"
        )
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("version") != CATALOG_VERSION:
        raise ValueError(
            f"schema catalog version {catalog.get('version')} != {CATALOG_VERSION}; "
            "please refresh"
        )
    return catalog


def _summary(catalog: Dict[str, Any]) -> str:
    columns = [c for t in catalog["tables"] for c in t["columns"]]
    commented = [c for c in columns if c["comment"]]
    enums = [c for c in columns if c.get("enum")]
    return (
        f"tables={len(catalog['tables'])} columns={len(columns)} "
        f"commented={len(commented)} enums={len(enums)}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Schema catalog utility")
    sub = parser.add_subparsers(dest="command", required=True)

    refresh_cmd = sub.add_parser("refresh", help="rebuild schema_catalog.json")
    refresh_cmd.add_argument("--out", default=str(DEFAULT_OUT))
    refresh_cmd.add_argument("--annotations", default=str(DEFAULT_ANNOTATIONS))

    args = parser.parse_args(argv)
    if args.command == "refresh":
        path = refresh(out=args.out, annotations=args.annotations)
        catalog = load_catalog(path)
        size = path.stat().st_size
        print(f"{_summary(catalog)}")
        print(f"wrote {path} ({size:,} bytes)")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
