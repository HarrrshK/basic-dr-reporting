from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Area, Doctor, FollowUpStatus, Product, Visit, VisitProduct

router = APIRouter(tags=["analytics"])


def period(start: date | None, end: date | None):
    end = end or date.today(); start = start or end.replace(day=1)
    return start, end


def doctor_conditions(area: str | None = None, hq: str | None = None, category: str | None = None,
                      active: bool | None = None):
    conditions = []
    if area: conditions.append(Doctor.area.has(Area.name == area))
    if hq: conditions.append(Doctor.hq == hq)
    if category: conditions.append(Doctor.category == category)
    if active is not None: conditions.append(Doctor.active == active)
    return conditions


@router.get("/analytics/dashboard")
def dashboard(start: date | None = None, end: date | None = None, area: str | None = None,
              hq: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    start, end = period(start, end); today = date.today(); week = today - timedelta(days=today.weekday())
    scope = doctor_conditions(area, hq, category)
    active_scope = doctor_conditions(area, hq, category, True)
    total = db.scalar(select(func.count(Doctor.id)).where(*scope)) or 0
    active = db.scalar(select(func.count(Doctor.id)).where(*active_scope)) or 0
    visits_period = db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope, Visit.visit_date.between(start, end))) or 0
    visited_period = db.scalar(select(func.count(func.distinct(Visit.doctor_id))).join(Doctor).where(*active_scope, Visit.visit_date.between(start, end))) or 0
    return {"total_doctors": total, "active_doctors": active, "inactive_doctors": total-active,
        "total_visits": db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope)) or 0,
        "visits_this_week": db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope, Visit.visit_date.between(week, today))) or 0,
        "visits_this_month": db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope, Visit.visit_date.between(today.replace(day=1), today))) or 0,
        "visited_doctors_in_period": visited_period, "visits_in_period": visits_period, "coverage_percentage": round(visited_period*100/active, 1) if active else 0,
        "areas_covered": db.scalar(select(func.count(func.distinct(Doctor.area_id))).select_from(Doctor).join(Visit, Visit.doctor_id == Doctor.id).where(*scope, Visit.visit_date.between(start,end))) or 0,
        "upcoming_follow_ups": db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope, Visit.follow_up_required, Visit.follow_up_status == FollowUpStatus.pending, Visit.follow_up_date >= today)) or 0,
        "overdue_follow_ups": db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope, Visit.follow_up_required, Visit.follow_up_status == FollowUpStatus.pending, Visit.follow_up_date < today)) or 0}


@router.get("/analytics/coverage")
def coverage(start: date | None = None, end: date | None = None, area: str | None = None,
             hq: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    start, end = period(start, end)
    counts = db.execute(select(Doctor.id, func.count(Visit.id).label("n")).outerjoin(Visit, (Visit.doctor_id == Doctor.id) & Visit.visit_date.between(start,end)).where(*doctor_conditions(area,hq,category,True)).group_by(Doctor.id)).all()
    freq = {"not_visited": 0, "once": 0, "two_to_five": 0, "six_to_ten": 0, "more_than_ten": 0}
    for _, n in counts:
        freq["not_visited" if n == 0 else "once" if n == 1 else "two_to_five" if n <= 5 else "six_to_ten" if n <= 10 else "more_than_ten"] += 1
    visited = len(counts)-freq["not_visited"]
    return {"active_doctors": len(counts), "visited": visited, "not_visited": freq["not_visited"], "coverage_percentage": round(visited*100/len(counts),1) if counts else 0, "frequency": freq}


@router.get("/areas")
def areas(start: date | None = None, end: date | None = None, hq: str | None = None,
          area: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    start, end = period(start, end)
    rows = db.execute(select(Area.id, Area.name, func.count(func.distinct(Doctor.id)).label("doctors"),
        func.count(func.distinct(case((Doctor.active, Doctor.id)))).label("active_doctors"), func.count(Visit.id).label("visits"),
        func.count(func.distinct(Visit.doctor_id)).label("visited_doctors")).select_from(Area).outerjoin(Doctor, Doctor.area_id == Area.id).outerjoin(Visit, (Visit.doctor_id==Doctor.id)&Visit.visit_date.between(start,end)).where(*doctor_conditions(area,hq,category)).group_by(Area.id).order_by(Area.name)).all()
    return [{**dict(r._mapping), "not_visited": r.active_doctors-r.visited_doctors,
             "coverage_percentage": round(r.visited_doctors*100/r.active_doctors,1) if r.active_doctors else 0,
             "average_visits_per_doctor": round(r.visits/r.active_doctors,2) if r.active_doctors else 0} for r in rows]


@router.get("/follow-ups")
def follow_ups(status: str = "pending", due_from: date | None = None, due_to: date | None = None,
               area: str | None = None, hq: str | None = None, doctor_id: int | None = None,
               db: Session = Depends(get_db)):
    stmt = select(Visit).join(Doctor).where(Visit.follow_up_required)
    if status: stmt = stmt.where(Visit.follow_up_status == status)
    if due_from: stmt = stmt.where(Visit.follow_up_date >= due_from)
    if due_to: stmt = stmt.where(Visit.follow_up_date <= due_to)
    if area: stmt = stmt.where(Doctor.area.has(Area.name == area))
    if hq: stmt = stmt.where(Doctor.hq == hq)
    if doctor_id: stmt = stmt.where(Visit.doctor_id == doctor_id)
    visits = list(db.scalars(stmt.order_by(Visit.follow_up_date)).all())
    return [{"id": v.id, "doctor_id": v.doctor_id, "doctor_name": v.doctor.name,
             "area": v.doctor.area.name if v.doctor.area else None, "follow_up_date": v.follow_up_date,
             "reason": v.follow_up_reason, "status": v.follow_up_status, "visit_date": v.visit_date} for v in visits]


@router.patch("/follow-ups/{visit_id}")
def update_follow_up(visit_id: int, status: FollowUpStatus, db: Session = Depends(get_db)):
    visit = db.get(Visit, visit_id)
    if not visit or not visit.follow_up_required:
        raise HTTPException(404, "Follow-up not found")
    visit.follow_up_status = status
    db.commit()
    return {"id": visit.id, "status": visit.follow_up_status}


@router.get("/analytics/trend")
def trend(start: date | None = None, end: date | None = None, area: str | None = None,
          hq: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    start, end = period(start, end)
    rows = db.execute(select(Visit.visit_date, func.count(Visit.id).label("visits"), func.count(func.distinct(Visit.doctor_id)).label("doctors"))
        .join(Doctor).where(*doctor_conditions(area,hq,category), Visit.visit_date.between(start,end)).group_by(Visit.visit_date).order_by(Visit.visit_date)).all()
    return [dict(r._mapping) for r in rows]


@router.get("/analytics/insights")
def insights(start: date | None = None, end: date | None = None, area: str | None = None,
             hq: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    start, end = period(start, end)
    span = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span - 1)
    scope = doctor_conditions(area, hq, category)
    active_scope = doctor_conditions(area, hq, category, True)

    def visit_count(date_start, date_end):
        return db.scalar(select(func.count(Visit.id)).join(Doctor).where(*scope, Visit.visit_date.between(date_start, date_end))) or 0

    current_visits = visit_count(start, end)
    previous_visits = visit_count(previous_start, previous_end)
    unique_doctors = db.scalar(select(func.count(func.distinct(Visit.doctor_id))).join(Doctor)
        .where(*scope, Visit.visit_date.between(start, end))) or 0
    active_doctors = db.scalar(select(func.count(Doctor.id)).where(*active_scope)) or 0

    top_doctors = db.execute(select(Doctor.id, Doctor.name, Area.name.label("area"), func.count(Visit.id).label("visits"),
        func.max(Visit.visit_date).label("last_visit")).join(Visit).outerjoin(Area).where(*scope, Visit.visit_date.between(start,end))
        .group_by(Doctor.id, Area.name).order_by(func.count(Visit.id).desc(), Doctor.name).limit(10)).all()
    top_products = db.execute(select(Product.id, Product.name, func.count(Visit.id).label("visits"),
        func.count(func.distinct(Visit.doctor_id)).label("doctors")).join(VisitProduct, VisitProduct.product_id == Product.id)
        .join(Visit, Visit.id == VisitProduct.visit_id).join(Doctor, Doctor.id == Visit.doctor_id)
        .where(*scope, Visit.visit_date.between(start,end)).group_by(Product.id).order_by(func.count(Visit.id).desc()).limit(10)).all()
    category_rows = db.execute(select(func.coalesce(Doctor.category, "Unspecified").label("category"),
        func.count(func.distinct(Doctor.id)).label("doctors"), func.count(func.distinct(Visit.doctor_id)).label("visited"),
        func.count(Visit.id).label("visits")).select_from(Doctor)
        .outerjoin(Visit, (Visit.doctor_id == Doctor.id) & Visit.visit_date.between(start,end))
        .where(*active_scope).group_by(func.coalesce(Doctor.category, "Unspecified")).order_by(func.count(Visit.id).desc())).all()
    attention_rows = db.execute(select(Doctor.id, Doctor.name, Area.name.label("area"), func.max(Visit.visit_date).label("last_visit"),
        func.count(Visit.id).label("total_visits")).select_from(Doctor).outerjoin(Visit).outerjoin(Area)
        .where(*active_scope).group_by(Doctor.id, Area.name)).all()
    attention = [{"id": row.id, "name": row.name, "area": row.area, "last_visit": row.last_visit,
                  "days_since_last_visit": (date.today() - row.last_visit).days if row.last_visit else None,
                  "total_visits": row.total_visits} for row in attention_rows]
    attention.sort(key=lambda row: (row["last_visit"] is not None, -(row["days_since_last_visit"] or 0)))

    weekday_counts = {name: 0 for name in ("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")}
    for visit_date in db.scalars(select(Visit.visit_date).join(Doctor).where(*scope, Visit.visit_date.between(start,end))):
        weekday_counts[visit_date.strftime("%A")] += 1
    change = None if previous_visits == 0 else round((current_visits - previous_visits) * 100 / previous_visits, 1)
    return {"period": {"start": start, "end": end, "previous_start": previous_start, "previous_end": previous_end},
        "kpis": {"visits": current_visits, "previous_visits": previous_visits, "visit_change_percentage": change,
                 "unique_doctors": unique_doctors, "active_doctors": active_doctors,
                 "coverage_percentage": round(unique_doctors * 100 / active_doctors, 1) if active_doctors else 0,
                 "repeat_visits": max(current_visits - unique_doctors, 0),
                 "average_visits_per_covered_doctor": round(current_visits / unique_doctors, 2) if unique_doctors else 0},
        "top_doctors": [dict(row._mapping) for row in top_doctors],
        "top_products": [dict(row._mapping) for row in top_products],
        "category_coverage": [{**dict(row._mapping), "coverage_percentage": round(row.visited*100/row.doctors,1) if row.doctors else 0} for row in category_rows],
        "weekday_distribution": [{"day": day, "visits": visits} for day, visits in weekday_counts.items()],
        "attention": attention[:15]}
