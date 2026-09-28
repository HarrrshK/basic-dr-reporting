from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import engine, get_db
from ..models import BackupAgentState, BackupChange
from ..services.backup_archive import make_backup_document, restore_backup_document, validate_backup_document

router = APIRouter(tags=["backup"])


class BackupHeartbeat(BaseModel):
    last_error: str | None = Field(default=None, max_length=1000)


class BackupAck(BaseModel):
    cursor: int = Field(ge=0)


class BackupImport(BaseModel):
    document: dict


def _state(db: Session) -> BackupAgentState:
    state = db.get(BackupAgentState, 1)
    if state is None:
        state = BackupAgentState(id=1, cursor=0, bootstrap_complete=False)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


@router.get("/health/db")
def database_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "connected", "database_available": True}
    except Exception:
        return {"status": "offline", "database_available": False}


@router.get("/backup/status")
def backup_status(db: Session = Depends(get_db)):
    state = _state(db)
    cursor = state.cursor or 0
    pending = db.scalar(select(func.count(BackupChange.id)).where(BackupChange.id > cursor)) or 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(900, settings.backup_interval_seconds * 3))
    connected = bool(state.last_seen_at and state.last_seen_at >= cutoff)
    return {
        "laptop_connected": connected,
        "last_successful_backup": state.last_successful_backup,
        "last_external_backup_at": state.last_external_backup_at,
        "pending_changes": pending,
        "last_error": state.last_error,
        "backup_requested": bool(state.requested_at),
        "external_backup_configured": settings.external_backup_configured,
        "external_backup_provider": settings.backup_provider if settings.external_backup_configured else None,
    }


@router.post("/backup/request", status_code=202)
def request_backup(db: Session = Depends(get_db)):
    state = _state(db)
    state.requested_at = datetime.now(timezone.utc)
    db.commit()
    return {"requested": True, "laptop_connected": bool(state.last_seen_at)}


@router.get("/backup/agent/status")
def agent_status(db: Session = Depends(get_db)):
    state = _state(db)
    max_change = db.scalar(select(func.max(BackupChange.id))) or 0
    return {
        "cursor": state.cursor or 0,
        "bootstrap_complete": state.bootstrap_complete,
        "requested": bool(state.requested_at),
        "max_change_id": max_change,
        "batch_size": settings.backup_batch_size,
    }


@router.post("/backup/agent/heartbeat")
def agent_heartbeat(payload: BackupHeartbeat, db: Session = Depends(get_db)):
    state = _state(db)
    state.last_seen_at = datetime.now(timezone.utc)
    state.last_error = payload.last_error
    db.commit()
    return {"ok": True}


@router.get("/backup/agent/bootstrap")
def agent_bootstrap(force: bool = False, db: Session = Depends(get_db)):
    state = _state(db)
    if state.bootstrap_complete and not force:
        return {"required": False, "cursor": state.cursor}
    cursor = db.scalar(select(func.max(BackupChange.id))) or 0
    document = make_backup_document(db)
    return {"required": True, "cursor": cursor, "document": document}


@router.get("/backup/agent/changes")
def agent_changes(after_id: int = Query(ge=0), limit: int = Query(500, ge=1, le=1000),
                  db: Session = Depends(get_db)):
    state = _state(db)
    if not state.bootstrap_complete:
        raise HTTPException(409, "Complete the initial laptop backup first")
    rows = list(db.scalars(select(BackupChange).where(BackupChange.id > after_id)
                           .order_by(BackupChange.id).limit(limit)))
    return {
        "changes": [{"id": row.id, "table_name": row.table_name, "record_id": row.record_id,
                     "operation": row.operation, "row_data": row.row_data} for row in rows],
        "next_cursor": rows[-1].id if rows else after_id,
        "has_more": len(rows) == limit,
    }


@router.post("/backup/agent/ack")
def agent_acknowledge(payload: BackupAck, db: Session = Depends(get_db)):
    state = _state(db)
    if not state.bootstrap_complete:
        raise HTTPException(409, "Complete the initial laptop backup first")
    current = state.cursor or 0
    maximum = db.scalar(select(func.max(BackupChange.id))) or 0
    if payload.cursor < current or payload.cursor > maximum:
        raise HTTPException(409, "Backup cursor is outside the available change range")
    if payload.cursor > current:
        state.cursor = payload.cursor
    state.last_successful_backup = datetime.now(timezone.utc)
    state.last_error = None
    state.requested_at = None
    db.commit()
    return {"cursor": state.cursor, "last_successful_backup": state.last_successful_backup}


@router.post("/backup/agent/bootstrap/ack")
def acknowledge_bootstrap(payload: BackupAck, db: Session = Depends(get_db)):
    state = _state(db)
    maximum = db.scalar(select(func.max(BackupChange.id))) or 0
    if payload.cursor < (state.cursor or 0) or payload.cursor > maximum:
        raise HTTPException(409, "Bootstrap cursor is outside the available change range")
    state.cursor = payload.cursor
    state.bootstrap_complete = True
    state.last_successful_backup = datetime.now(timezone.utc)
    state.last_seen_at = datetime.now(timezone.utc)
    state.last_error = None
    state.requested_at = None
    db.commit()
    return {"bootstrap_complete": True, "cursor": state.cursor}


@router.get("/backup/export")
def export_backup(db: Session = Depends(get_db)):
    document = make_backup_document(db)
    return JSONResponse(document, headers={
        "Content-Disposition": f"attachment; filename=field-reports-{document['export_date']}.json"
    })


@router.post("/backup/archive")
def archive_backup(payload: BackupImport, db: Session = Depends(get_db)):
    if not settings.external_backup_configured:
        raise HTTPException(409, "External backup is not configured")
    try:
        validate_backup_document(payload.document)
    except (TypeError, ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from error
    try:
        import boto3

        document = payload.document
        prefix = settings.backup_prefix.strip("/")
        filename = f"{document['export_date']}.json"
        key = f"{prefix}/{filename}" if prefix else filename
        client = boto3.client(
            "s3", region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            endpoint_url=settings.aws_endpoint_url or None,
        )
        client.put_object(Bucket=settings.backup_bucket, Key=key,
                          Body=(json.dumps(document, indent=2) + "\n").encode(),
                          ContentType="application/json")
    except Exception as error:
        raise HTTPException(502, "External JSON backup upload failed; production data was not changed") from error
    state = _state(db)
    state.last_external_backup_at = datetime.now(timezone.utc)
    db.commit()
    return {"uploaded": True, "provider": "s3", "object_key": key,
            "export_date": document["export_date"]}


@router.post("/backup/import")
def import_backup(payload: BackupImport, db: Session = Depends(get_db)):
    try:
        summary = restore_backup_document(db, payload.document)
        db.commit()
    except (TypeError, ValueError, KeyError) as error:
        db.rollback()
        raise HTTPException(422, str(error)) from error
    return {"restored": summary, "idempotent": True}
