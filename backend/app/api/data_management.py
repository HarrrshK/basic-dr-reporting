from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Area, Doctor, ImportBatch, Product, Visit, VisitProduct

router = APIRouter(prefix="/data-management", tags=["data-management"])

CONFIRMATIONS = {
    "visits": "DELETE ALL VISITS",
    "doctors": "DELETE ALL DOCTORS AND VISITS",
    "products": "DELETE ALL PRODUCTS",
    "follow_ups": "DELETE ALL FOLLOW UPS",
    "imports": "DELETE IMPORT HISTORY",
    "areas": "DELETE ALL AREAS",
    "everything": "RESET EVERYTHING",
}


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    count = lambda model: db.scalar(select(func.count()).select_from(model)) or 0
    return {"doctors": count(Doctor), "active_doctors": db.scalar(select(func.count(Doctor.id)).where(Doctor.active)) or 0,
            "visits": count(Visit), "products": count(Product), "areas": count(Area), "imports": count(ImportBatch),
            "follow_ups": db.scalar(select(func.count(Visit.id)).where(Visit.follow_up_required)) or 0}


@router.delete("/purge/{category}")
def purge(category: str, confirm: str, db: Session = Depends(get_db)):
    expected = CONFIRMATIONS.get(category)
    if not expected:
        raise HTTPException(404, "Unknown data category")
    if confirm != expected:
        raise HTTPException(400, f"Type {expected} to confirm")
    before = summary(db)
    if category in {"visits", "doctors", "everything"}:
        db.execute(delete(VisitProduct))
        db.execute(delete(Visit))
    if category in {"doctors", "everything"}:
        db.execute(delete(Doctor))
        db.execute(delete(Area))
    if category in {"products", "everything"}:
        db.execute(delete(VisitProduct))
        db.execute(delete(Product))
    if category == "follow_ups":
        db.execute(update(Visit).where(Visit.follow_up_required).values(
            follow_up_required=False, follow_up_date=None, follow_up_reason=None, follow_up_status=None))
    if category in {"imports", "everything"}:
        db.execute(delete(ImportBatch))
    if category == "areas":
        db.execute(update(Doctor).values(area_id=None))
        db.execute(delete(Area))
    db.commit()
    return {"category": category, "removed_from": before, "remaining": summary(db)}


@router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    links = db.scalar(select(func.count()).select_from(VisitProduct).where(VisitProduct.product_id == product_id)) or 0
    db.execute(delete(VisitProduct).where(VisitProduct.product_id == product_id))
    db.delete(product); db.commit()
    return {"id": product_id, "removed_visit_links": links}


@router.delete("/areas/{area_id}")
def delete_area(area_id: int, db: Session = Depends(get_db)):
    area = db.get(Area, area_id)
    if not area:
        raise HTTPException(404, "Area not found")
    doctors = db.scalar(select(func.count(Doctor.id)).where(Doctor.area_id == area_id)) or 0
    db.execute(update(Doctor).where(Doctor.area_id == area_id).values(area_id=None))
    db.delete(area); db.commit()
    return {"id": area_id, "doctors_unassigned": doctors}
