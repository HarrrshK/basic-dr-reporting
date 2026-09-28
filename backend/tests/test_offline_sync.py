import json
import threading
import time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ProcessedSyncOperation, Product
from app.sync.dispatcher import CommandDispatcher
from app.sync.queue import QueueRepository
from app.sync.service import SyncService


def build_sync(tmp_path, handler=None):
    queue = QueueRepository(f"sqlite:///{tmp_path / 'queue.db'}", max_backoff=2); queue.initialize()
    remote = create_engine(f"sqlite:///{tmp_path / 'permanent.db'}")
    Base.metadata.create_all(remote)
    sessions = sessionmaker(bind=remote, expire_on_commit=False)
    dispatcher = CommandDispatcher()
    dispatcher.register("test", handler or (lambda db, operation, payload: db.add(Product(name=payload["name"]))))
    return queue, remote, sessions, SyncService(queue, dispatcher, sessions, remote, batch_size=50)


def count_products(sessions):
    with sessions() as db: return db.scalar(select(func.count(Product.id))) or 0


def test_available_database_synchronizes_and_removes_queue_only_after_commit(tmp_path):
    queue, _, sessions, service = build_sync(tmp_path)
    operation = queue.enqueue("test", "one", "create", json.dumps({"name":"One"}))
    assert queue.pending_count() == 1
    assert service.run_once() == 1
    assert count_products(sessions) == 1
    assert queue.pending_count() == 0
    assert queue.get(operation) is None


def test_unavailable_database_keeps_durable_record_across_restart(tmp_path):
    queue, _, _, service = build_sync(tmp_path)
    operation = queue.enqueue("test", "offline", "create", json.dumps({"name":"Offline"}))
    service.check_database = lambda: False
    assert service.run_once() == 0
    restarted = QueueRepository(f"sqlite:///{tmp_path / 'queue.db'}"); restarted.initialize()
    assert restarted.pending_count() == 1
    assert restarted.get(operation).payload == json.dumps({"name":"Offline"})


def test_database_recovery_synchronizes_pending_record(tmp_path):
    queue, _, sessions, service = build_sync(tmp_path)
    queue.enqueue("test", "recovered", "create", json.dumps({"name":"Recovered"}))
    original = service.check_database; service.check_database = lambda: False
    assert service.run_once() == 0
    service.check_database = original
    assert service.run_once() == 1
    assert count_products(sessions) == 1 and queue.pending_count() == 0


def test_transaction_failure_rolls_back_and_retains_queue(tmp_path):
    def failing(db, operation, payload):
        db.add(Product(name="Must Roll Back")); db.flush(); raise RuntimeError("transaction failed")
    queue, _, sessions, service = build_sync(tmp_path, failing)
    operation = queue.enqueue("test", "bad", "create", "{}")
    service.run_once()
    assert count_products(sessions) == 0
    record = queue.get(operation)
    assert record and record.retry_count == 1 and "transaction failed" in record.last_error


def test_restart_with_available_database_drains_existing_queue(tmp_path):
    queue, remote, sessions, service = build_sync(tmp_path)
    queue.enqueue("test", "restart", "create", json.dumps({"name":"After Restart"}))
    new_queue = QueueRepository(f"sqlite:///{tmp_path / 'queue.db'}"); new_queue.initialize()
    dispatcher = CommandDispatcher(); dispatcher.register("test", lambda db, op, payload: db.add(Product(name=payload["name"])))
    restarted = SyncService(new_queue, dispatcher, sessions, remote)
    assert restarted.run_once() == 1
    assert count_products(sessions) == 1 and new_queue.pending_count() == 0


def test_duplicate_processing_is_idempotent(tmp_path):
    queue, _, sessions, service = build_sync(tmp_path)
    queue.enqueue("test", "same", "create", json.dumps({"name":"Exactly Once"}), operation_id="11111111-1111-1111-1111-111111111111")
    record = queue.claim("worker", 1)[0]
    service._apply(record); service._apply(record)
    assert count_products(sessions) == 1
    with sessions() as db: assert db.scalar(select(func.count(ProcessedSyncOperation.operation_id))) == 1


def test_one_failed_record_does_not_block_valid_records(tmp_path):
    def selective(db, operation, payload):
        if payload["name"] == "Bad": raise ValueError("bad record")
        db.add(Product(name=payload["name"]))
    queue, _, sessions, service = build_sync(tmp_path, selective)
    bad = queue.enqueue("test", "bad", "create", json.dumps({"name":"Bad"}))
    queue.enqueue("test", "good1", "create", json.dumps({"name":"Good 1"}))
    queue.enqueue("test", "good2", "create", json.dumps({"name":"Good 2"}))
    assert service.run_once() == 2
    assert count_products(sessions) == 2
    assert queue.get(bad).retry_count == 1


def test_failed_entity_preserves_order_while_other_entities_continue(tmp_path):
    attempted = []
    def selective(db, operation, payload):
        attempted.append(payload["name"])
        if payload["name"] == "First": raise ValueError("first failed")
        db.add(Product(name=payload["name"]))
    queue, _, sessions, service = build_sync(tmp_path, selective)
    queue.enqueue("test", "same", "create", json.dumps({"name": "First"}))
    queue.enqueue("test", "same", "update", json.dumps({"name": "Must Wait"}))
    queue.enqueue("test", "other", "create", json.dumps({"name": "Other"}))
    assert service.run_once() == 1
    assert attempted == ["First", "Other"]
    assert count_products(sessions) == 1
    assert queue.pending_count() == 2


def test_timeout_keeps_data_pending(tmp_path):
    queue, _, sessions, service = build_sync(tmp_path, lambda db, op, payload: (_ for _ in ()).throw(TimeoutError("network timeout")))
    operation = queue.enqueue("test", "timeout", "create", "{}")
    service.run_once()
    assert count_products(sessions) == 0
    assert queue.get(operation) is not None


def test_concurrent_synchronizers_do_not_process_same_record_twice(tmp_path):
    queue, remote, sessions, first = build_sync(tmp_path)
    queue.enqueue("test", "concurrent", "create", json.dumps({"name":"Concurrent"}))
    dispatcher = first.dispatcher
    second = SyncService(queue, dispatcher, sessions, remote)
    results = []
    threads = [threading.Thread(target=lambda service=service: results.append(service.run_once())) for service in (first, second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(results) == 1
    assert count_products(sessions) == 1 and queue.pending_count() == 0


def test_background_worker_automatically_synchronizes(tmp_path):
    queue, _, sessions, service = build_sync(tmp_path)
    queue.enqueue("test", "automatic", "create", json.dumps({"name": "Automatic"}))
    service.start()
    deadline = time.monotonic() + 2
    while queue.pending_count() and time.monotonic() < deadline:
        time.sleep(0.01)
    service.stop()
    assert count_products(sessions) == 1
    assert queue.pending_count() == 0
