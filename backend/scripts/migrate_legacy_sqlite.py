"""Safely copy application rows from an existing SQLite file to PostgreSQL."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
from enum import Enum
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.models import Area, Doctor, ImportBatch, Product, Visit, VisitProduct

TABLE_MODELS = (Area, Product, ImportBatch, Doctor, Visit, VisitProduct)
BATCH_SIZE = 500


def _canonical(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _source_rows(connection, model, source_columns: set[str]) -> tuple[list[dict], list[str]]:
    table = model.__table__
    names = [column.name for column in table.columns if column.name in source_columns]
    missing = []
    for column in table.columns:
        if column.name in source_columns or column.primary_key or column.nullable:
            continue
        if column.default is None and column.server_default is None:
            missing.append(column.name)
    if missing:
        raise ValueError(f"SQLite {table.name} is missing required column(s): {', '.join(missing)}")
    rows = [dict(row) for row in connection.execute(select(*(table.c[name] for name in names))).mappings()]
    if not names or any(any(column.name not in row for column in table.primary_key.columns) for row in rows):
        raise ValueError(f"SQLite {table.name} does not expose its required primary key")
    return rows, names


def _insert_statement(dialect: str, table, rows: list[dict]):
    if dialect == "postgresql":
        return pg_insert(table).values(rows).on_conflict_do_nothing()
    if dialect == "sqlite":
        return sqlite_insert(table).values(rows).on_conflict_do_nothing()
    raise ValueError(f"Unsupported migration target database: {dialect}")


def migrate(source_path: Path, target_url: str | None = None) -> dict[str, dict[str, int]]:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")
    source = create_engine(f"sqlite:///{source_path.resolve()}")
    target = create_engine(target_url or settings.sqlalchemy_database_url, pool_pre_ping=True)
    result: dict[str, dict[str, int]] = {}
    try:
        source_inspector = inspect(source)
        source_tables = set(source_inspector.get_table_names())
        for model in TABLE_MODELS:
            name = model.__tablename__
            if name not in source_tables:
                raise ValueError(f"SQLite source is missing required table: {name}")
            expected_pk = [column.name for column in model.__table__.primary_key.columns]
            actual_pk = source_inspector.get_pk_constraint(name).get("constrained_columns") or []
            if actual_pk != expected_pk:
                raise ValueError(f"SQLite table {name} has primary key {actual_pk}; expected {expected_pk}")

        target_names = set(inspect(target).get_table_names())
        missing_target = [model.__tablename__ for model in TABLE_MODELS if model.__tablename__ not in target_names]
        if missing_target:
            raise ValueError("Run Alembic migrations on PostgreSQL first; missing table(s): " + ", ".join(missing_target))

        with source.connect() as source_connection, target.begin() as target_connection:
            dialect = target.dialect.name
            for model in TABLE_MODELS:
                table = model.__table__
                source_columns = {column["name"] for column in source_inspector.get_columns(table.name)}
                rows, source_names = _source_rows(source_connection, model, source_columns)
                pk_names = [column.name for column in table.primary_key.columns]
                if any(any(row.get(name) is None for name in pk_names) for row in rows):
                    raise ValueError(f"SQLite table {table.name} contains a row without an ID")

                inserted = already_present = 0
                target_columns = {column["name"] for column in inspect(target).get_columns(table.name)}
                common_names = [name for name in source_names if name in target_columns]
                comparable_names = [name for name in common_names if name not in pk_names]
                for offset in range(0, len(rows), BATCH_SIZE):
                    batch = rows[offset:offset + BATCH_SIZE]
                    keys = [tuple(row[name] for name in pk_names) for row in batch]
                    selected = [*table.primary_key.columns,
                                *(table.c[name] for name in comparable_names)]
                    target_rows = target_connection.execute(
                        select(*selected).where(tuple_(*table.primary_key.columns).in_(keys))
                    ).mappings().all() if keys else []
                    existing = {tuple(row[name] for name in pk_names): dict(row) for row in target_rows}
                    to_insert = []
                    for source_row, key in zip(batch, keys):
                        current = existing.get(key)
                        if current is None:
                            to_insert.append(source_row)
                            continue
                        different = [name for name in comparable_names
                                     if _canonical(current[name]) != _canonical(source_row.get(name))]
                        if different:
                            raise ValueError(
                                f"Conflicting existing {table.name} ID {key}; fields differ: {', '.join(different)}"
                            )
                        already_present += 1
                    if to_insert:
                        target_connection.execute(_insert_statement(dialect, table, to_insert))
                        inserted += len(to_insert)
                source_count = len(rows)
                target_count = target_connection.scalar(select(func.count()).select_from(table)) or 0
                result[table.name] = {"source_rows": source_count, "inserted": inserted,
                                      "already_present": already_present, "target_rows": target_count}

            if dialect == "postgresql":
                for table_name in ("areas", "products", "doctors", "visits"):
                    target_connection.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                        f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table_name}"
                    ))

            checks = {
                "visits_without_doctor": target_connection.scalar(
                    select(func.count()).select_from(Visit.__table__.outerjoin(
                        Doctor.__table__, Visit.__table__.c.doctor_id == Doctor.__table__.c.id
                    )).where(Doctor.__table__.c.id.is_(None))
                ) or 0,
                "visit_products_without_visit": target_connection.scalar(
                    select(func.count()).select_from(VisitProduct.__table__.outerjoin(
                        Visit.__table__, VisitProduct.__table__.c.visit_id == Visit.__table__.c.id
                    )).where(Visit.__table__.c.id.is_(None))
                ) or 0,
                "visit_products_without_product": target_connection.scalar(
                    select(func.count()).select_from(VisitProduct.__table__.outerjoin(
                        Product.__table__, VisitProduct.__table__.c.product_id == Product.__table__.c.id
                    )).where(Product.__table__.c.id.is_(None))
                ) or 0,
            }
            if any(checks.values()):
                raise ValueError(f"Post-migration relationship verification failed: {checks}")
            result["relationship_checks"] = checks
        return result
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default="dr_reporting.db", type=Path)
    args = parser.parse_args()
    for table, detail in migrate(args.source).items():
        print(f"{table}: {detail}")
