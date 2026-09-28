from datetime import datetime, timezone
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..database import engine, get_db
from ..models import Area, Doctor, ImportBatch, Product, Visit
from ..services.sync_transfer import build_snapshot, replace_database_snapshot
from ..sync.runtime import queue_repository

router = APIRouter(tags=["synchronization"])


class AgentFailure(BaseModel):
    error: str


class BootstrapRequest(BaseModel):
    snapshot: dict


def _queue_snapshot(db: Session) -> dict:
    snapshot = build_snapshot(db)
    existing = queue_repository.latest_pending_snapshot()
    if existing:
        try:
            queued_snapshot = json.loads(existing.payload)
            if queued_snapshot.get("revision") == snapshot.get("revision"):
                return {"operation_id": existing.operation_id, **queue_repository.snapshot_status()}
        except (TypeError, ValueError):
            pass
    operation_id = str(uuid4())
    queue_repository.enqueue(
        "snapshot", "application", "replace", json.dumps(snapshot, separators=(",", ":")), operation_id
    )
    return {"operation_id": operation_id, **queue_repository.snapshot_status()}


@router.get("/health/db")
def database_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "connected", "database_available": True}
    except Exception:
        return {"status": "offline", "database_available": False}


@router.get("/sync/status")
def synchronization_status():
    state = queue_repository.snapshot_status()
    seen_at = state.get("laptop_agent_seen_at")
    try:
        seen = datetime.fromisoformat(seen_at) if seen_at else None
        state["laptop_agent_online"] = bool(seen and (datetime.now(timezone.utc) - seen).total_seconds() <= 20)
    except (TypeError, ValueError):
        state["laptop_agent_online"] = False
    state["syncing"] = queue_repository.has_claimed_records()
    return state


@router.post("/sync/flush", status_code=202)
def flush_to_laptop(db: Session = Depends(get_db)):
    if not queue_repository.bootstrap_complete():
        raise HTTPException(409, "Start the laptop sync agent and complete initial database setup before flushing")
    return _queue_snapshot(db)


@router.post("/sync/trigger", status_code=202)
def trigger_synchronization(db: Session = Depends(get_db)):
    if not queue_repository.bootstrap_complete():
        raise HTTPException(409, "Start the laptop sync agent and complete initial database setup before flushing")
    return _queue_snapshot(db)


@router.post("/sync/agent/heartbeat")
def agent_heartbeat(worker_id: str):
    queue_repository.touch_agent(worker_id)
    return {"ok": True}


@router.post("/sync/agent/bootstrap")
def bootstrap_render_database(payload: BootstrapRequest, db: Session = Depends(get_db)):
    if queue_repository.bootstrap_complete():
        return {"initialized": False, "ready": True, "reason": "already_initialized"}
    existing = sum(
        db.scalar(select(func.count()).select_from(model)) or 0
        for model in (Area, Doctor, Product, Visit, ImportBatch)
    )
    if existing:
        queue_repository.set_metadata("laptop_bootstrap_blocked", "1")
        return {"initialized": False, "ready": False, "reason": "render_workspace_contains_data"}
    replace_database_snapshot(db, payload.snapshot)
    db.commit()
    revision, _ = queue_repository.revisions()
    queue_repository.mark_bootstrap_complete(revision)
    return {"initialized": existing == 0, "ready": True, "revision": revision}


@router.post("/sync/agent/claim")
def claim_flush(worker_id: str):
    queue_repository.touch_agent(worker_id)
    rows = queue_repository.claim(worker_id, 1)
    if not rows:
        return {"operation": None}
    record = rows[0]
    return {
        "operation": {
            "operation_id": record.operation_id,
            "entity_type": record.entity_type,
            "operation": record.operation,
            "payload": record.payload,
            "retry_count": record.retry_count,
        }
    }


@router.post("/sync/agent/{operation_id}/ack")
def acknowledge_flush(operation_id: str):
    if not queue_repository.acknowledge_operation(operation_id):
        raise HTTPException(404, "Pending flush request not found")
    return {"acknowledged": True, **queue_repository.snapshot_status()}


@router.post("/sync/agent/{operation_id}/fail")
def fail_flush(operation_id: str, payload: AgentFailure):
    if not queue_repository.get(operation_id):
        raise HTTPException(404, "Pending flush request not found")
    queue_repository.fail_operation(operation_id, payload.error)
    return {"retained": True, **queue_repository.snapshot_status()}
