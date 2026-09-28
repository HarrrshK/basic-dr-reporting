from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..database import engine
from ..models import ProcessedSyncOperation
from .dispatcher import CommandDispatcher
from .queue import QueueRepository, SyncQueue


class SyncService:
    def __init__(self, queue: QueueRepository, dispatcher: CommandDispatcher,
                 permanent_sessions=None, permanent_engine=None, batch_size: int | None = None):
        self.queue = queue
        self.dispatcher = dispatcher
        self.engine = permanent_engine or engine
        self.sessions = permanent_sessions or sessionmaker(bind=self.engine, expire_on_commit=False)
        self.batch_size = batch_size or settings.sync_batch_size
        self.worker_id = str(uuid4())
        self._lock = threading.Lock()
        self._stop = threading.Event(); self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._syncing = False; self._postgres_available = False
        self._last_health_check: datetime | None = None
        self._connection_failures = 0

    def check_database(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            self._postgres_available = True
        except SQLAlchemyError:
            self._postgres_available = False
        self._last_health_check = datetime.now(timezone.utc)
        return self._postgres_available

    def run_once(self) -> int:
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            if not self.check_database():
                return 0
            self._syncing = True; completed = 0
            try:
                records = self.queue.claim(self.worker_id, self.batch_size)
                blocked_entities: set[tuple[str, str]] = set()
                for record in records:
                    entity_key = (record.entity_type, record.entity_id)
                    if entity_key in blocked_entities:
                        self.queue.release(record.id)
                        continue
                    try:
                        self._apply(record)
                        self.queue.acknowledge(record.id)
                        completed += 1
                    except Exception as exc:
                        self.queue.fail(record.id, str(exc))
                        blocked_entities.add(entity_key)
                return completed
            finally:
                self._syncing = False
        finally:
            self._lock.release()

    def _apply(self, record: SyncQueue):
        with self.sessions.begin() as db:
            processed = db.get(ProcessedSyncOperation, record.operation_id)
            if processed:
                return
            self.dispatcher.dispatch(db, record.entity_type, record.operation, record.payload)
            db.add(ProcessedSyncOperation(operation_id=record.operation_id, entity_type=record.entity_type))

    def start(self):
        self.queue.initialize()
        self._stop.clear()
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="sqlite-postgres-sync", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set(); self._wake.set()
        if self._thread:
            self._thread.join()

    def wake(self):
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            self.run_once()
            if self._postgres_available:
                self._connection_failures = 0
                delay = settings.sync_poll_seconds
            else:
                self._connection_failures += 1
                delay = min(2 ** (self._connection_failures - 1), settings.sync_max_backoff_seconds)
            self._wake.wait(timeout=delay)
            self._wake.clear()

    def status(self) -> dict:
        return {"postgres_available": self._postgres_available, "pending_records": self.queue.pending_count(),
                "syncing": self._syncing, "last_successful_sync": self.queue.last_successful_sync(),
                "last_health_check": self._last_health_check}
