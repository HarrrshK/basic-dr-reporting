from __future__ import annotations

import json
from datetime import date, datetime, time
from enum import Enum

from sqlalchemy import Date as SQLDate, DateTime as SQLDateTime, Time as SQLTime, delete, select, text
from sqlalchemy.orm import Session

from ..models import Area, Doctor, FollowUpStatus, ImportBatch, Product, Visit, VisitProduct
from ..sync.queue import SyncMetadata


SNAPSHOT_MODELS = (Area, Product, ImportBatch, Doctor, Visit, VisitProduct)


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    raise TypeError(f"Unsupported snapshot value: {type(value).__name__}")


def build_snapshot(db: Session) -> dict:
    revision = db.get(SyncMetadata, "data_revision") if db.bind and db.bind.dialect.name == "sqlite" else None
    records = {}
    for model in SNAPSHOT_MODELS:
        table = model.__table__
        rows = db.scalars(select(model).order_by(*table.primary_key.columns)).all()
        records[table.name] = [
            {column.name: getattr(row, column.name) for column in table.columns}
            for row in rows
        ]
    encoded = json.loads(json.dumps(records, default=_json_value, separators=(",", ":")))
    return {"revision": int(revision.value) if revision else 0, "records": encoded}


def _typed_values(model, values: dict) -> dict:
    converted = {}
    for column in model.__table__.columns:
        value = values.get(column.name)
        if value is None:
            converted[column.name] = None
            continue
        column_type = column.type
        enum_class = getattr(column_type, "enum_class", None)
        if enum_class and isinstance(value, str):
            value = enum_class(value)
        elif isinstance(column_type, SQLDateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif isinstance(column_type, SQLDate) and isinstance(value, str):
            value = date.fromisoformat(value)
        elif isinstance(column_type, SQLTime) and isinstance(value, str):
            value = time.fromisoformat(value)
        converted[column.name] = value
    return converted


def replace_database_snapshot(db: Session, snapshot: dict) -> None:
    records = snapshot.get("records")
    if not isinstance(records, dict):
        raise ValueError("Snapshot is missing its records map")

    for model in (VisitProduct, Visit, Doctor, Product, Area, ImportBatch):
        db.execute(delete(model))
    for model in SNAPSHOT_MODELS:
        rows = records.get(model.__tablename__, [])
        if not isinstance(rows, list):
            raise ValueError(f"Invalid rows for {model.__tablename__}")
        db.add_all(model(**_typed_values(model, row)) for row in rows)
        db.flush()

    if db.bind and db.bind.dialect.name == "postgresql":
        for table_name in ("areas", "products", "doctors", "visits"):
            db.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table_name}"
            ))
