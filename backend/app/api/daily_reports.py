from calendar import monthrange
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Area, DailyActivity, Doctor, Visit
from ..schemas import DailyActivityCreate

router = APIRouter(tags=["daily reports"])


@router.get("/daily-reports/{report_date}")
def get_daily_report(report_date: date, db: Session = Depends(get_db)):
    activity = db.scalar(select(DailyActivity).where(DailyActivity.report_date == report_date))
    calls = list(db.scalars(select(Visit).options(
        selectinload(Visit.doctor).selectinload(Doctor.area), selectinload(Visit.products))
        .where(Visit.visit_date == report_date).order_by(Visit.visit_time, Visit.id)))
    inferred_type = "field_work" if calls and activity is None else None
    return {
        "report_date": report_date,
        "activity": ({"id": activity.id, "activity_type": activity.activity_type,
                      "description": activity.description} if activity else None),
        "inferred_activity_type": inferred_type,
        "calls": calls,
        "call_count": len(calls),
        "unique_doctors": len({call.doctor_id for call in calls}),
    }


@router.put("/daily-reports/{report_date}")
def save_daily_report(report_date: date, payload: DailyActivityCreate,
                      db: Session = Depends(get_db)):
    if payload.report_date != report_date:
        raise HTTPException(422, "Report date in the URL and body must match")
    activity = db.scalar(select(DailyActivity).where(DailyActivity.report_date == report_date))
    if activity is None:
        activity = DailyActivity(report_date=report_date)
        db.add(activity)
    activity.activity_type = payload.activity_type
    activity.description = payload.description
    db.commit()
    db.refresh(activity)
    return {"id": activity.id, "report_date": activity.report_date,
            "activity_type": activity.activity_type, "description": activity.description}


@router.get("/analytics/missed-calls")
def missed_calls(month: str, hq: str | None = None, area: str | None = None,
                 db: Session = Depends(get_db)):
    try:
        month_start = date.fromisoformat(f"{month}-01")
    except ValueError as error:
        raise HTTPException(422, "Month must use YYYY-MM format") from error
    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])

    scope = [Doctor.active.is_(True)]
    if hq:
        scope.append(Doctor.hq == hq)
    if area:
        scope.append(Doctor.area.has(Area.name == area))

    calls_in_month = (select(Visit.doctor_id).where(
        Visit.visit_date.between(month_start, month_end)).distinct())
    doctors = list(db.scalars(select(Doctor).options(selectinload(Doctor.area))
        .where(*scope, ~Doctor.id.in_(calls_in_month)).order_by(Doctor.name)))
    doctor_ids = [doctor.id for doctor in doctors]
    history = {}
    if doctor_ids:
        rows = db.execute(select(Visit.doctor_id, func.max(Visit.visit_date).label("last_visit"),
            func.count(Visit.id).label("total_visits")).where(Visit.doctor_id.in_(doctor_ids),
            Visit.visit_date < month_start).group_by(Visit.doctor_id)).all()
        history = {row.doctor_id: row for row in rows}

    items = []
    for doctor in doctors:
        row = history.get(doctor.id)
        last_visit = row.last_visit if row else None
        items.append({
            "id": doctor.id, "name": doctor.name, "external_id": doctor.external_id,
            "area": doctor.area.name if doctor.area else None, "hq": doctor.hq,
            "category": doctor.category, "specialty": doctor.specialty_group or doctor.existing_specialty,
            "clinic_hospital": doctor.clinic_hospital, "mobile": doctor.mobile,
            "last_visit": last_visit,
            "days_since_last_visit_at_month_end": (month_end - last_visit).days if last_visit else None,
            "previous_visits": int(row.total_visits) if row else 0,
            "never_visited": last_visit is None,
        })
    items.sort(key=lambda item: (not item["never_visited"], item["last_visit"] or date.min, item["name"].casefold()))
    active_count = db.scalar(select(func.count(Doctor.id)).where(*scope)) or 0
    return {
        "month": month, "month_start": month_start, "month_end": month_end,
        "scope": {"hq": hq, "area": area}, "active_doctors": active_count,
        "missed_calls": len(items), "covered_doctors": active_count - len(items),
        "coverage_percentage": round((active_count - len(items)) * 100 / active_count, 1) if active_count else 0,
        "never_visited": sum(item["never_visited"] for item in items),
        "previously_visited": sum(not item["never_visited"] for item in items),
        "items": items,
    }
