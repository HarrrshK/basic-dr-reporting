"""Copy the pre-offline-queue application database into PostgreSQL.

This utility is intentionally repeat-safe: rows already present in PostgreSQL are
left untouched.  It copies source tables in dependency order and advances the
PostgreSQL integer sequences after preserving the legacy primary keys.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import MetaData, create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert

from app.config import settings


TABLE_ORDER = (
    "areas",
    "products",
    "import_batches",
    "doctors",
    "visits",
    "visit_products",
)


def migrate(source_path: Path) -> dict[str, int]:
    if not source_path.is_file():
        raise FileNotFoundError(f"Legacy SQLite database not found: {source_path}")

    source = create_engine(f"sqlite:///{source_path.resolve()}")
    target = create_engine(settings.permanent_database_url, pool_pre_ping=True)
    source_meta = MetaData()
    target_meta = MetaData()
    source_meta.reflect(bind=source)
    target_meta.reflect(bind=target)

    target_names = set(inspect(target).get_table_names())
    copied: dict[str, int] = {}
    with source.connect() as source_connection, target.begin() as target_connection:
        for table_name in TABLE_ORDER:
            if table_name not in source_meta.tables or table_name not in target_names:
                continue
            source_table = source_meta.tables[table_name]
            target_table = target_meta.tables[table_name]
            common_columns = [column.name for column in target_table.columns if column.name in source_table.c]
            rows = [dict(row._mapping) for row in source_connection.execute(
                select(*(source_table.c[name] for name in common_columns))
            )]
            if rows:
                before = target_connection.scalar(select(func.count()).select_from(target_table)) or 0
                target_connection.execute(insert(target_table).values(rows).on_conflict_do_nothing())
                after = target_connection.scalar(select(func.count()).select_from(target_table)) or 0
                copied[table_name] = after - before
            else:
                copied[table_name] = 0

        for table_name in ("areas", "products", "doctors", "visits"):
            if table_name not in target_names:
                continue
            target_connection.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table_name}"
            ))

    source.dispose()
    target.dispose()
    return copied


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default="dr_reporting.db", type=Path)
    args = parser.parse_args()
    for table, count in migrate(args.source).items():
        print(f"{table}: {count} row(s) copied")
