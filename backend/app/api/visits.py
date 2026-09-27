from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Area, Doctor, FollowUpStatus, Product, Visit
from ..schemas import BulkVisitCreate, VisitCreate, VisitOut

router = APIRouter(prefix="/visits", tags=["visits"])


def load_products(db: Session, ids: list[int]) -> list[Product]:
    products = list(db.scalars(select(Product).where(Product.id.in_(set(ids))))) if ids else []
    if len(products) != len(set(ids)): raise HTTPException(422, "One or more products do not exist")
    return products


@router.post("", response_model=VisitOut, status_code=201)
def create_visit(payload: VisitCreate, db: Session = Depends(get_db)):
    if not db.get(Doctor, payload.doctor_id): raise HTTPException(422, "Doctor does not exist")
    values = payload.model_dump(exclude={"product_ids"})
    if payload.follow_up_required: values["follow_up_status"] = FollowUpStatus.pending
    visit = Visit(**values, products=load_products(db, payload.product_ids))
    db.add(visit); db.commit(); db.refresh(visit)
    return visit


@router.post("/bulk", status_code=201)
def create_bulk_visits(payload: BulkVisitCreate, db: Session = Depends(get_db)):
    doctors = list(db.scalars(select(Doctor).where(Doctor.id.in_(payload.doctor_ids))))
    found = {doctor.id for doctor in doctors}
    missing = sorted(set(payload.doctor_ids) - found)
    if missing:
        raise HTTPException(422, f"Doctors do not exist: {missing}")
    inactive = sorted(doctor.id for doctor in doctors if not doctor.active)
    if inactive:
        raise HTTPException(422, f"Inactive doctors cannot receive new visits: {inactive}")
    products = load_products(db, payload.product_ids)
    values = payload.model_dump(exclude={"doctor_ids", "product_ids"})
    if payload.follow_up_required:
        values["follow_up_status"] = FollowUpStatus.pending
    visits = [Visit(doctor_id=doctor_id, **values, products=list(products)) for doctor_id in payload.doctor_ids]
    db.add_all(visits)
    db.commit()
    return {"created": len(visits), "visit_ids": [visit.id for visit in visits],
            "doctor_ids": payload.doctor_ids}


@router.get("")
def list_visits(date_from: date | None = None, date_to: date | None = None, doctor_id: int | None = None,
                area: str | None = None, hq: str | None = None, category: str | None = None,
                product_id: int | None = None, purpose: str | None = None, outcome: str | None = None,
                follow_up_required: bool | None = None, q: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db)):
    stmt = select(Visit).options(selectinload(Visit.doctor).selectinload(Doctor.area), selectinload(Visit.products))
    if date_from: stmt = stmt.where(Visit.visit_date >= date_from)
    if date_to: stmt = stmt.where(Visit.visit_date <= date_to)
    if doctor_id: stmt = stmt.where(Visit.doctor_id == doctor_id)
    doctor_joined = False
    if area or hq or category or q:
        stmt = stmt.join(Doctor); doctor_joined = True
    if area: stmt = stmt.join(Area).where(Area.name.ilike(area))
    if hq: stmt = stmt.where(Doctor.hq == hq)
    if category: stmt = stmt.where(Doctor.category == category)
    if product_id: stmt = stmt.join(Visit.products).where(Product.id == product_id)
    if purpose: stmt = stmt.where(Visit.purpose == purpose)
    if outcome: stmt = stmt.where(Visit.outcome == outcome)
    if follow_up_required is not None: stmt = stmt.where(Visit.follow_up_required == follow_up_required)
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Doctor.name.ilike(term), Visit.purpose.ilike(term), Visit.outcome.ilike(term), Visit.notes.ilike(term)))
    count_stmt = select(func.count()).select_from(stmt.with_only_columns(Visit.id).distinct().order_by(None).subquery())
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Visit.visit_date.desc(), Visit.visit_time.desc()).offset((page-1)*page_size).limit(page_size)).unique())
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/filter-options")
def visit_filter_options(db: Session = Depends(get_db)):
    def values(column):
        return list(db.scalars(select(column).where(column.is_not(None), column != "").distinct().order_by(column)))
    products = list(db.scalars(select(Product).where(Product.active).order_by(Product.name)))
    return {"purposes": values(Visit.purpose), "outcomes": values(Visit.outcome),
            "products": [{"id": product.id, "name": product.name} for product in products]}


@router.put("/{visit_id}", response_model=VisitOut)
def update_visit(visit_id: int, payload: VisitCreate, db: Session = Depends(get_db)):
    visit = db.scalar(select(Visit).options(selectinload(Visit.products)).where(Visit.id == visit_id))
    if not visit: raise HTTPException(404, "Visit not found")
    if not db.get(Doctor, payload.doctor_id): raise HTTPException(422, "Doctor does not exist")
    for key, value in payload.model_dump(exclude={"product_ids"}).items(): setattr(visit, key, value)
    visit.follow_up_status = FollowUpStatus.pending if payload.follow_up_required else None
    visit.products = load_products(db, payload.product_ids); db.commit(); db.refresh(visit)
    return visit


@router.delete("/{visit_id}", status_code=204)
def delete_visit(visit_id: int, db: Session = Depends(get_db)):
    visit = db.get(Visit, visit_id)
    if not visit: raise HTTPException(404, "Visit not found")
    db.delete(visit); db.commit()
