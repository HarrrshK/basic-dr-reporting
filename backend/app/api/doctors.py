from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Area, Doctor, Visit
from ..schemas import DoctorCreate, DoctorOut, DoctorUpdate
from ..services.doctors import apply_doctor_values, normalize_text

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.get("")
def list_doctors(q: str | None = None, area: str | None = None, hq: str | None = None,
                 category: str | None = None, active: bool | None = None, specialty_group: str | None = None,
                 doctor_status: str | None = None, qualification: str | None = None, gender: str | None = None,
                 sort: str = "name", direction: str = "asc", page: int = Query(1, ge=1),
                 page_size: int = Query(25, ge=1, le=200), db: Session = Depends(get_db)):
    stmt = select(Doctor).options(selectinload(Doctor.area))
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Doctor.external_id.ilike(term), Doctor.name.ilike(term), Doctor.mobile.ilike(term),
            Doctor.clinic_hospital.ilike(term), Doctor.existing_specialty.ilike(term), Doctor.area.has(Area.name.ilike(term)), Doctor.hq.ilike(term)))
    if area: stmt = stmt.where(Doctor.area.has(Area.name.ilike(area)))
    for field, value in ((Doctor.hq, hq), (Doctor.category, category), (Doctor.specialty_group, specialty_group),
                         (Doctor.doctor_status, doctor_status), (Doctor.qualification, qualification), (Doctor.gender, gender)):
        if value: stmt = stmt.where(field == value)
    if active is not None: stmt = stmt.where(Doctor.active == active)
    count = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    sort_column = {"name": Doctor.name, "area": Area.name, "hq": Doctor.hq, "category": Doctor.category}.get(sort, Doctor.name)
    if sort == "area": stmt = stmt.outerjoin(Area)
    stmt = stmt.order_by(sort_column.desc() if direction == "desc" else sort_column.asc()).offset((page - 1) * page_size).limit(page_size)
    return {"items": [DoctorOut.model_validate(x) for x in db.scalars(stmt).unique()], "total": count, "page": page, "page_size": page_size}


@router.delete("")
def clear_doctor_master(confirm: str, db: Session = Depends(get_db)):
    if confirm != "DELETE ALL DOCTORS":
        raise HTTPException(400, "Type DELETE ALL DOCTORS to confirm")
    doctors = list(db.scalars(select(Doctor).options(selectinload(Doctor.visits))))
    deleted = deactivated = 0
    for doctor in doctors:
        if doctor.visits:
            doctor.active = False
            deactivated += 1
        else:
            db.delete(doctor)
            deleted += 1
    db.flush()
    db.execute(delete(Area).where(~Area.doctors.any()))
    db.commit()
    return {"deleted": deleted, "deactivated": deactivated,
            "message": "Doctors with visit history were deactivated so historical visits remain valid."}


@router.post("", response_model=DoctorOut, status_code=201)
def create_doctor(payload: DoctorCreate, db: Session = Depends(get_db)):
    doctor = Doctor(name=payload.name.strip(), normalized_name=normalize_text(payload.name))
    apply_doctor_values(db, doctor, payload.model_dump())
    db.add(doctor); db.commit(); db.refresh(doctor)
    return doctor


@router.get("/filter-options")
def filter_options(hq: str | None = None, area: str | None = None, active: bool | None = None,
                   db: Session = Depends(get_db)):
    base = []
    if hq:
        base.append(Doctor.hq == hq)
    if area:
        base.append(Doctor.area.has(Area.name == area))
    if active is not None:
        base.append(Doctor.active == active)

    def values(column, *conditions):
        return list(db.scalars(select(column).where(column.is_not(None), column != "", *conditions)
                               .distinct().order_by(column)))

    area_conditions = [Doctor.active == active] if active is not None else []
    if hq:
        area_conditions.append(Doctor.hq == hq)
    area_rows = db.execute(select(Area.id, Area.name, func.count(Doctor.id).label("doctor_count"))
        .join(Doctor).where(*area_conditions).group_by(Area.id).order_by(Area.name)).all()
    doctors = db.execute(select(Doctor.id, Doctor.external_id, Doctor.name, Area.name.label("area"), Doctor.hq,
                                Doctor.clinic_hospital, Doctor.existing_specialty)
        .outerjoin(Area).where(*base).order_by(Doctor.name)).all()
    return {
        "hqs": values(Doctor.hq, *( [Doctor.active == active] if active is not None else [])),
        "areas": [dict(row._mapping) for row in area_rows],
        "categories": values(Doctor.category, *base),
        "specialty_groups": values(Doctor.specialty_group, *base),
        "existing_specialties": values(Doctor.existing_specialty, *base),
        "doctor_statuses": values(Doctor.doctor_status, *base),
        "qualifications": values(Doctor.qualification, *base),
        "genders": values(Doctor.gender, *base),
        "clinics": values(Doctor.clinic_hospital, *base),
        "doctors": [dict(row._mapping) for row in doctors],
    }


@router.get("/{doctor_id}")
def doctor_profile(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.scalar(select(Doctor).options(selectinload(Doctor.area), selectinload(Doctor.visits).selectinload(Visit.products)).where(Doctor.id == doctor_id))
    if not doctor: raise HTTPException(404, "Doctor not found")
    visits = sorted(doctor.visits, key=lambda v: (v.visit_date, v.visit_time or time.min), reverse=True)
    dates = sorted(v.visit_date for v in visits)
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    today = date.today()
    metrics = {"total_visits": len(visits), "visits_this_month": sum(v.visit_date.year == today.year and v.visit_date.month == today.month for v in visits),
        "visits_this_year": sum(v.visit_date.year == today.year for v in visits), "first_visit_date": dates[0] if dates else None,
        "most_recent_visit_date": dates[-1] if dates else None, "days_since_last_visit": (today - dates[-1]).days if dates else None,
        "average_visit_interval": round(sum(gaps) / len(gaps), 1) if gaps else None, "longest_gap": max(gaps) if gaps else None,
        "shortest_gap": min(gaps) if gaps else None, "average_visits_per_month": round(len(visits) / max(1, ((today.year - dates[0].year) * 12 + today.month - dates[0].month + 1)), 2) if dates else 0,
        "products_discussed": len({p.id for v in visits for p in v.products}), "pending_follow_ups": sum(v.follow_up_required and (v.follow_up_status is None or v.follow_up_status.value == "pending") for v in visits)}
    return {"doctor": DoctorOut.model_validate(doctor), "metrics": metrics, "visits": visits}


@router.patch("/{doctor_id}", response_model=DoctorOut)
def update_doctor(doctor_id: int, payload: DoctorUpdate, db: Session = Depends(get_db)):
    doctor = db.get(Doctor, doctor_id)
    if not doctor: raise HTTPException(404, "Doctor not found")
    apply_doctor_values(db, doctor, payload.model_dump(exclude_unset=True)); db.commit(); db.refresh(doctor)
    return doctor


@router.delete("/{doctor_id}")
def remove_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor = db.scalar(select(Doctor).options(selectinload(Doctor.visits)).where(Doctor.id == doctor_id))
    if not doctor:
        raise HTTPException(404, "Doctor not found")
    if doctor.visits:
        doctor.active = False
        action = "deactivated"
    else:
        db.delete(doctor)
        action = "deleted"
    db.commit()
    return {"id": doctor_id, "action": action,
            "message": "Visit history was preserved." if action == "deactivated" else "Doctor removed."}
