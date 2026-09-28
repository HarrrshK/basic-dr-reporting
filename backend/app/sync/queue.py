from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, create_engine, event, exists, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column, sessionmaker

from ..config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QueueBase(DeclarativeBase):
    pass


class SyncQueue(QueueBase):
    __tablename__ = "sync_queue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(100), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncMetadata(QueueBase):
    __tablename__ = "sync_metadata"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class QueueRepository:
    def __init__(self, url: str | None = None, max_backoff: int | None = None):
        configured_app_db = settings.database_url or ""
        queue_url = url or settings.sqlite_queue_url or (
            configured_app_db if configured_app_db.startswith("sqlite") else "sqlite:///./offline_queue.db"
        )
        if not queue_url.startswith("sqlite"):
            raise ValueError("The durable synchronization queue must use SQLite")
        self.engine = create_engine(queue_url, connect_args={"check_same_thread": False}, pool_pre_ping=True)

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.max_backoff = max_backoff or settings.sync_max_backoff_seconds

    def initialize(self):
        QueueBase.metadata.create_all(self.engine)
        with self.sessions.begin() as db:
            for record in db.scalars(select(SyncQueue).where(SyncQueue.synced_at.is_not(None))):
                db.delete(record)

    def enqueue(self, entity_type: str, entity_id: str, operation: str, payload: str,
                operation_id: str | None = None) -> str:
        operation_id = operation_id or str(uuid4())
        with self.sessions.begin() as db:
            existing = db.scalar(select(SyncQueue).where(SyncQueue.operation_id == operation_id))
            if not existing:
                db.add(SyncQueue(operation_id=operation_id, entity_type=entity_type, entity_id=entity_id,
                                 operation=operation, payload=payload))
        return operation_id

    def claim(self, worker_id: str, limit: int, stale_after_seconds: int = 120) -> list[SyncQueue]:
        now = utcnow(); stale = now - timedelta(seconds=stale_after_seconds)
        claimed: list[SyncQueue] = []
        with self.sessions.begin() as db:
            db.execute(update(SyncQueue).where(SyncQueue.synced_at.is_(None), SyncQueue.locked_at < stale)
                       .values(locked_at=None, locked_by=None))
            prior = aliased(SyncQueue)
            has_earlier_entity_operation = exists(select(prior.id).where(
                prior.synced_at.is_(None), prior.id < SyncQueue.id,
                prior.entity_type == SyncQueue.entity_type, prior.entity_id == SyncQueue.entity_id,
            ))
            ids = list(db.scalars(select(SyncQueue.id).where(SyncQueue.synced_at.is_(None),
                SyncQueue.locked_at.is_(None), SyncQueue.available_at <= now,
                ~has_earlier_entity_operation).order_by(SyncQueue.id).limit(limit)))
            for record_id in ids:
                changed = db.execute(update(SyncQueue).where(SyncQueue.id == record_id, SyncQueue.locked_at.is_(None))
                                     .values(locked_at=now, locked_by=worker_id)).rowcount
                if changed:
                    claimed.append(db.get(SyncQueue, record_id))
        return claimed

    def release(self, record_id: int):
        with self.sessions.begin() as db:
            db.execute(update(SyncQueue).where(SyncQueue.id == record_id, SyncQueue.synced_at.is_(None))
                       .values(locked_at=None, locked_by=None))

    def acknowledge(self, record_id: int):
        synced_at = utcnow()
        with self.sessions.begin() as db:
            record = db.get(SyncQueue, record_id)
            if record:
                record.synced_at = synced_at
                record.locked_at = None; record.locked_by = None
                db.flush()
                if record.entity_type == "snapshot":
                    payload = json.loads(record.payload)
                    revision = str(payload.get("revision", 0))
                    flushed = db.get(SyncMetadata, "last_flushed_revision")
                    if flushed:
                        flushed.value = revision
                    else:
                        db.add(SyncMetadata(key="last_flushed_revision", value=revision))
                db.delete(record)
            metadata = db.get(SyncMetadata, "last_successful_sync")
            if metadata:
                metadata.value = synced_at.isoformat()
            else:
                db.add(SyncMetadata(key="last_successful_sync", value=synced_at.isoformat()))

    def acknowledge_operation(self, operation_id: str) -> bool:
        record = self.get(operation_id)
        if not record:
            return False
        self.acknowledge(record.id)
        return True

    def fail_operation(self, operation_id: str, error: str):
        record = self.get(operation_id)
        if record:
            self.fail(record.id, error)

    def set_metadata(self, key: str, value: str):
        with self.sessions.begin() as db:
            record = db.get(SyncMetadata, key)
            if record:
                record.value = value
            else:
                db.add(SyncMetadata(key=key, value=value))

    def metadata(self, key: str, default: str | None = None) -> str | None:
        with self.sessions() as db:
            value = db.get(SyncMetadata, key)
            return value.value if value else default

    def revisions(self) -> tuple[int, int]:
        return int(self.metadata("data_revision", "0") or 0), int(self.metadata("last_flushed_revision", "0") or 0)

    def bootstrap_complete(self) -> bool:
        return self.metadata("laptop_bootstrap_complete") == "1"

    def mark_bootstrap_complete(self, revision: int):
        self.set_metadata("laptop_bootstrap_complete", "1")
        self.set_metadata("laptop_bootstrap_blocked", "0")
        self.set_metadata("data_revision", str(revision))
        self.set_metadata("last_flushed_revision", str(revision))

    def touch_agent(self, worker_id: str):
        self.set_metadata("laptop_agent_id", worker_id)
        self.set_metadata("laptop_agent_seen_at", utcnow().isoformat())

    def snapshot_status(self) -> dict:
        revision, flushed_revision = self.revisions()
        pending_records = self.pending_count()
        return {
            "pending_records": pending_records,
            "dirty": revision > flushed_revision,
            "revision": revision,
            "last_flushed_revision": flushed_revision,
            "flush_requested": pending_records > 0,
            "last_successful_sync": self.last_successful_sync(),
            "laptop_agent_seen_at": self.metadata("laptop_agent_seen_at"),
            "bootstrap_complete": self.bootstrap_complete(),
            "bootstrap_blocked": self.metadata("laptop_bootstrap_blocked") == "1",
        }

    def fail(self, record_id: int, error: str):
        with self.sessions.begin() as db:
            record = db.get(SyncQueue, record_id)
            if not record: return
            record.retry_count += 1
            delay = min(2 ** max(record.retry_count - 1, 0), self.max_backoff)
            record.available_at = utcnow() + timedelta(seconds=delay)
            record.last_error = error[:4000]
            record.locked_at = None; record.locked_by = None

    def pending_count(self) -> int:
        with self.sessions() as db:
            return db.scalar(select(func.count(SyncQueue.id)).where(SyncQueue.synced_at.is_(None))) or 0

    def has_claimed_records(self) -> bool:
        with self.sessions() as db:
            return bool(db.scalar(select(func.count(SyncQueue.id)).where(
                SyncQueue.synced_at.is_(None), SyncQueue.locked_at.is_not(None)
            )))

    def last_successful_sync(self) -> str | None:
        with self.sessions() as db:
            value = db.get(SyncMetadata, "last_successful_sync")
            return value.value if value else None

    def get(self, operation_id: str) -> SyncQueue | None:
        with self.sessions() as db:
            record = db.scalar(select(SyncQueue).where(SyncQueue.operation_id == operation_id))
            if record: db.expunge(record)
            return record

    def latest_pending_snapshot(self) -> SyncQueue | None:
        with self.sessions() as db:
            record = db.scalar(select(SyncQueue).where(
                SyncQueue.entity_type == "snapshot", SyncQueue.synced_at.is_(None)
            ).order_by(SyncQueue.id.desc()).limit(1))
            if record:
                db.expunge(record)
            return record
