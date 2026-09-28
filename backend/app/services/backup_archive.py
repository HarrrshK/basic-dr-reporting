from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from enum import Enum

from sqlalchemy import Date as SQLDate, DateTime as SQLDateTime, Time as SQLTime, delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import Area, Doctor, ImportBatch, Product, Visit, VisitProduct

EXPORT_VERSION = 1
SNAPSHOT_MODELS = (Area, Product, ImportBatch, Doctor, Visit, VisitProduct)
MODEL_BY_TABLE = {model.__tablename__: model for model in SNAPSHOT_MODELS}
MODEL_BY_TABLE["visit_products"] = VisitProduct


def _bind(db):
    return db.get_bind() if isinstance(db, Session) else db


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    raise TypeError(f"Unsupported backup value: {type(value).__name__}")


def build_records(db: Session) -> dict[str, list[dict]]:
    output = {}
    for model in SNAPSHOT_MODELS:
        table = model.__table__
        rows = db.execute(select(table).order_by(*table.primary_key.columns)).mappings().all()
        output[table.name] = [dict(row) for row in rows]
    return json.loads(json.dumps(output, default=_json_value, separators=(",", ":")))


def make_backup_document(db: Session, export_date: date | None = None) -> dict:
    return {
        "export_version": EXPORT_VERSION,
        "export_date": (export_date or date.today()).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": build_records(db),
    }


def _typed_value(column, value):
    if value is None:
        return None
    enum_class = getattr(column.type, "enum_class", None)
    if enum_class and isinstance(value, str):
        return enum_class(value)
    if isinstance(column.type, SQLDateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(column.type, SQLDate) and isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(column.type, SQLTime) and isinstance(value, str):
        return time.fromisoformat(value)
    return value


def validate_backup_document(document: dict) -> dict[str, list[dict]]:
    if not isinstance(document, dict):
        raise ValueError("Backup document must be a JSON object")
    if document.get("export_version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported backup export_version: {document.get('export_version')!r}")
    try:
        date.fromisoformat(document["export_date"])
        datetime.fromisoformat(document["generated_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Backup requires valid export_date and generated_at values") from error
    records = document.get("records")
    if not isinstance(records, dict) or set(records) != set(MODEL_BY_TABLE):
        raise ValueError("Backup records must contain every supported application table")

    validated = {}
    for table_name, model in MODEL_BY_TABLE.items():
        rows = records[table_name]
        if not isinstance(rows, list):
            raise ValueError(f"Backup table {table_name} must contain a list")
        table = model.__table__
        allowed = {column.name for column in table.columns}
        primary_key = [column.name for column in table.primary_key.columns]
        seen = set()
        checked_rows = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != allowed:
                raise ValueError(f"Backup row {table_name}[{index}] has missing or unknown fields")
            key = tuple(row.get(name) for name in primary_key)
            if any(value is None for value in key):
                raise ValueError(f"Backup row {table_name}[{index}] is missing its ID")
            if key in seen:
                raise ValueError(f"Backup contains duplicate ID {key} in {table_name}")
            seen.add(key)
            typed = {column.name: _typed_value(column, row[column.name]) for column in table.columns}
            checked_rows.append(typed)
        validated[table_name] = checked_rows
    return validated


def _upsert_rows(db: Session, model, rows: list[dict]) -> None:
    if not rows:
        return
    bind = _bind(db)
    dialect = bind.dialect.name if bind else ""
    if dialect == "postgresql":
        statement = pg_insert(model.__table__).values(rows)
    elif dialect == "sqlite":
        statement = sqlite_insert(model.__table__).values(rows)
    else:
        raise ValueError(f"Unsupported database for backup restore: {dialect}")
    key_names = {column.name for column in model.__table__.primary_key.columns}
    updates = {column.name: statement.excluded[column.name] for column in model.__table__.columns
               if column.name not in key_names}
    statement = (statement.on_conflict_do_update(index_elements=list(model.__table__.primary_key.columns),
                                                   set_=updates)
                 if updates else statement.on_conflict_do_nothing())
    db.execute(statement)


def restore_backup_document(db: Session, document: dict) -> dict:
    records = validate_backup_document(document)
    restored = {}
    for model in SNAPSHOT_MODELS:
        rows = records[model.__tablename__]
        _upsert_rows(db, model, rows)
        restored[model.__tablename__] = len(rows)
    _reset_sequences(db)
    return restored


def apply_backup_changes(db: Session, changes: list[dict]) -> None:
    for change in changes:
        table_name = change.get("table_name")
        model = MODEL_BY_TABLE.get(table_name)
        if model is None:
            raise ValueError(f"Unsupported backup table: {table_name}")
        table = model.__table__
        operation = change.get("operation")
        if operation == "upsert":
            row = change.get("row_data")
            if not isinstance(row, dict) or set(row) != {column.name for column in table.columns}:
                raise ValueError(f"Backup upsert for {table_name} has invalid row data")
            typed = {column.name: _typed_value(column, row[column.name]) for column in table.columns}
            _upsert_rows(db, model, [typed])
        elif operation == "delete":
            key = str(change.get("record_id", "")).split(":")
            columns = list(table.primary_key.columns)
            if len(key) != len(columns):
                raise ValueError(f"Backup delete for {table_name} has an invalid record ID")
            values = []
            for column, value in zip(columns, key):
                values.append(int(value) if column.type.python_type is int else value)
            db.execute(delete(model).where(*(column == value for column, value in zip(columns, values))))
        else:
            raise ValueError(f"Unsupported backup operation: {operation}")
    _reset_sequences(db)


def _reset_sequences(db: Session) -> None:
    if _bind(db).dialect.name != "postgresql":
        return
    for table_name in ("areas", "products", "doctors", "visits"):
        db.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table_name}"
        ))
