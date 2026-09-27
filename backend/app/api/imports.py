from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ImportBatch
from ..schemas import ImportConfirm
from ..services.imports import confirm_import, preview_import

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/preview")
async def preview(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Upload an .xlsx or .xlsm workbook")
    try:
        batch = preview_import(db, file.filename, await file.read())
        return {"id": batch.id, "filename": batch.filename, "mapping": batch.mapping,
                "summary": batch.summary, "rows": batch.rows}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{batch_id}/confirm")
def confirm(batch_id: str, payload: ImportConfirm, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "Import preview not found")
    try:
        return confirm_import(db, batch, payload.mapping, payload.resolutions)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

