import csv
from datetime import date
from io import StringIO

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Area, Doctor, Product, Visit

router = APIRouter(prefix="/exports", tags=["exports"])


def csv_response(name: str, headers: list[str], rows):
    output = StringIO(); writer = csv.writer(output); writer.writerow(headers); writer.writerows(rows)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/doctors.csv")
def doctors(area: str | None = None, hq: str | None = None, category: str | None = None,
            specialty_group: str | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    stmt = select(Doctor).options(selectinload(Doctor.area)).order_by(Doctor.name)
    if area: stmt = stmt.where(Doctor.area.has(name=area))
    if hq: stmt = stmt.where(Doctor.hq == hq)
    if category: stmt = stmt.where(Doctor.category == category)
    if specialty_group: stmt = stmt.where(Doctor.specialty_group == specialty_group)
    if active is not None: stmt = stmt.where(Doctor.active == active)
    records = db.scalars(stmt)
    return csv_response("doctors.csv", ["Doctor Name","Area","HQ","Specialty","Category","Mobile","Active","Clinic/Hospital"],
        ((d.name,d.area.name if d.area else "",d.hq,d.existing_specialty,d.category,d.mobile,d.active,d.clinic_hospital) for d in records))


@router.get("/visits.csv")
def visits(date_from: date | None = None, date_to: date | None = None, area: str | None = None,
           hq: str | None = None, doctor_id: int | None = None, product_id: int | None = None,
           db: Session = Depends(get_db)):
    stmt = select(Visit).options(selectinload(Visit.doctor).selectinload(Doctor.area),selectinload(Visit.products)).order_by(Visit.visit_date.desc())
    if date_from: stmt=stmt.where(Visit.visit_date>=date_from)
    if date_to: stmt=stmt.where(Visit.visit_date<=date_to)
    if area: stmt=stmt.join(Doctor).join(Area).where(Area.name == area)
    elif hq: stmt=stmt.join(Doctor)
    if hq: stmt=stmt.where(Doctor.hq == hq)
    if doctor_id: stmt=stmt.where(Visit.doctor_id == doctor_id)
    if product_id: stmt=stmt.join(Visit.products).where(Product.id == product_id)
    records=db.scalars(stmt)
    return csv_response("visits.csv",["Date","Time","Doctor","Area","Purpose","Products","Outcome","Notes","Follow-up Date"],
        ((v.visit_date,v.visit_time,v.doctor.name,v.doctor.area.name if v.doctor.area else "",v.purpose,", ".join(p.name for p in v.products),v.outcome,v.notes,v.follow_up_date) for v in records))
