from datetime import date, time

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import Area, Doctor, FollowUpStatus, ImportBatch, Product, Visit, VisitProduct
from ..services.doctors import apply_doctor_values, normalize_text
from .dispatcher import CommandDispatcher


def parse_date(value): return date.fromisoformat(value) if value else None
def parse_time(value): return time.fromisoformat(value) if value else None


def visit_handler(db: Session, operation: str, payload: dict):
    if operation in {"create", "bulk_create"}:
        doctor_ids = payload.get("doctor_ids") or [payload["doctor_id"]]
        doctors = list(db.scalars(select(Doctor).where(Doctor.id.in_(doctor_ids))))
        if {doctor.id for doctor in doctors} != set(doctor_ids): raise ValueError("One or more doctors no longer exist")
        if any(not doctor.active for doctor in doctors): raise ValueError("Inactive doctors cannot receive visits")
        product_ids = payload.get("product_ids", [])
        products = list(db.scalars(select(Product).where(Product.id.in_(product_ids)))) if product_ids else []
        if len(products) != len(set(product_ids)): raise ValueError("One or more products no longer exist")
        values = {key: payload.get(key) for key in ("purpose","outcome","notes","follow_up_reason")}
        values.update(visit_date=parse_date(payload["visit_date"]), visit_time=parse_time(payload.get("visit_time")),
                      follow_up_required=payload.get("follow_up_required",False), follow_up_date=parse_date(payload.get("follow_up_date")),
                      follow_up_status=FollowUpStatus.pending if payload.get("follow_up_required") else None)
        for doctor_id in doctor_ids:
            db.add(Visit(doctor_id=doctor_id, products=list(products), **values))
    elif operation == "update":
        visit = db.get(Visit, payload["id"])
        if not visit: raise ValueError("Visit no longer exists")
        for key in ("doctor_id","purpose","outcome","notes","follow_up_required","follow_up_reason"):
            if key in payload: setattr(visit,key,payload[key])
        if "visit_date" in payload: visit.visit_date=parse_date(payload["visit_date"])
        if "visit_time" in payload: visit.visit_time=parse_time(payload["visit_time"])
        if "follow_up_date" in payload: visit.follow_up_date=parse_date(payload["follow_up_date"])
        visit.follow_up_status=FollowUpStatus.pending if visit.follow_up_required else None
        if "product_ids" in payload:
            visit.products=list(db.scalars(select(Product).where(Product.id.in_(payload["product_ids"]))))
    elif operation == "delete":
        visit=db.get(Visit,payload["id"])
        if visit: db.delete(visit)
    else: raise ValueError(f"Unsupported visit operation: {operation}")


def doctor_handler(db: Session, operation: str, payload: dict):
    if operation == "create":
        doctor=Doctor(name=payload["name"].strip(),normalized_name=normalize_text(payload["name"]))
        apply_doctor_values(db,doctor,payload);db.add(doctor)
    elif operation == "update":
        doctor=db.get(Doctor,payload.pop("id"))
        if not doctor: raise ValueError("Doctor no longer exists")
        apply_doctor_values(db,doctor,payload)
    elif operation == "delete":
        doctor=db.get(Doctor,payload["id"])
        if doctor:
            if db.scalar(select(Visit.id).where(Visit.doctor_id==doctor.id).limit(1)): doctor.active=False
            else: db.delete(doctor)
    elif operation == "clear":
        doctor_ids_with_visits = select(Visit.doctor_id).distinct()
        db.execute(update(Doctor).where(Doctor.id.in_(doctor_ids_with_visits)).values(active=False))
        db.execute(delete(Doctor).where(Doctor.id.not_in(doctor_ids_with_visits)))
        db.execute(delete(Area).where(~Area.doctors.any()))
    else: raise ValueError(f"Unsupported doctor operation: {operation}")


def product_handler(db: Session, operation: str, payload: dict):
    if operation == "create": db.add(Product(name=payload["name"],description=payload.get("description")))
    elif operation == "delete":
        db.execute(delete(VisitProduct).where(VisitProduct.product_id==payload["id"]))
        product=db.get(Product,payload["id"])
        if product: db.delete(product)
    else: raise ValueError(f"Unsupported product operation: {operation}")


def follow_up_handler(db: Session, operation: str, payload: dict):
    visit=db.get(Visit,payload["visit_id"])
    if not visit: raise ValueError("Visit no longer exists")
    visit.follow_up_status=FollowUpStatus(payload["status"])


def data_handler(db: Session, operation: str, payload: dict):
    if operation == "delete_area":
        db.execute(update(Doctor).where(Doctor.area_id == payload["id"]).values(area_id=None))
        area = db.get(Area, payload["id"])
        if area: db.delete(area)
        return
    if operation != "purge": raise ValueError(f"Unsupported data operation: {operation}")
    category = payload["category"]
    if category in {"visits","doctors","everything"}:
        db.execute(delete(VisitProduct)); db.execute(delete(Visit))
    if category in {"doctors","everything"}:
        db.execute(delete(Doctor)); db.execute(delete(Area))
    if category in {"products","everything"}:
        db.execute(delete(VisitProduct)); db.execute(delete(Product))
    if category == "follow_ups":
        db.execute(update(Visit).where(Visit.follow_up_required).values(follow_up_required=False,
            follow_up_date=None,follow_up_reason=None,follow_up_status=None))
    if category in {"imports","everything"}: db.execute(delete(ImportBatch))
    if category == "areas":
        db.execute(update(Doctor).values(area_id=None)); db.execute(delete(Area))


def register_handlers(dispatcher: CommandDispatcher):
    dispatcher.register("visit",visit_handler)
    dispatcher.register("doctor",doctor_handler)
    dispatcher.register("product",product_handler)
    dispatcher.register("follow_up",follow_up_handler)
    dispatcher.register("data",data_handler)
