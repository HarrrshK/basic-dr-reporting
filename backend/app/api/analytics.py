from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

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


@router.get("/analytics/call-analysis")
def call_analysis(start: date | None = None, end: date | None = None, area: str | None = None,
                  hq: str | None = None, category: str | None = None, doctor_id: int | None = None,
                  product_id: int | None = None, purpose: str | None = None, outcome: str | None = None,
                  follow_up_required: bool | None = None,
                  db: Session = Depends(get_db)):
    """Detailed, derived call metrics for the selected reporting scope."""
    start, end = period(start, end)
    if start > end:
        raise HTTPException(422, "Start date must be on or before end date")
    scope = doctor_conditions(area, hq, category)
    if doctor_id is not None:
        scope.append(Doctor.id == doctor_id)

    doctors = list(db.scalars(select(Doctor).options(selectinload(Doctor.area))
                              .where(*scope).order_by(Doctor.name)))
    visit_query = (select(Visit).options(
        selectinload(Visit.products), selectinload(Visit.doctor).selectinload(Doctor.area))
        .join(Doctor).where(*scope, Visit.visit_date.between(start, end)))
    if product_id is not None:
        visit_query = visit_query.where(Visit.products.any(Product.id == product_id))
    if purpose:
        visit_query = visit_query.where(Visit.purpose == purpose)
    if outcome:
        visit_query = visit_query.where(Visit.outcome == outcome)
    if follow_up_required is not None:
        visit_query = visit_query.where(Visit.follow_up_required == follow_up_required)
    visits = list(db.scalars(visit_query.order_by(Visit.visit_date, Visit.visit_time, Visit.id)))

    today = date.today()
    doctor_visits: dict[int, list[Visit]] = defaultdict(list)
    product_counts: dict[int, dict] = {}
    purpose_counts: dict[str, int] = defaultdict(int)
    outcome_counts: dict[str, int] = defaultdict(int)
    weekday_counts = {day: 0 for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}
    time_counts = {"Before 9 AM": 0, "9 AM–12 PM": 0, "12 PM–3 PM": 0,
                   "3 PM–6 PM": 0, "After 6 PM": 0, "Time not recorded": 0}
    daily_counts: dict[date, dict] = defaultdict(lambda: {"visits": 0, "doctors": set()})
    area_groups: dict[str, dict] = defaultdict(lambda: {"doctors": set(), "active": set(), "visited": set(), "visited_active": set(), "visits": 0})
    hq_groups: dict[str, dict] = defaultdict(lambda: {"doctors": set(), "active": set(), "visited": set(), "visited_active": set(), "visits": 0})
    follow_up_counts = {"required": 0, "pending": 0, "completed": 0, "cancelled": 0,
                       "overdue": 0, "due_today": 0, "upcoming": 0}

    for doctor in doctors:
        area_name = doctor.area.name if doctor.area else "(No area)"
        hq_name = doctor.hq or "(No HQ)"
        area_groups[area_name]["doctors"].add(doctor.id)
        hq_groups[hq_name]["doctors"].add(doctor.id)
        if doctor.active:
            area_groups[area_name]["active"].add(doctor.id)
            hq_groups[hq_name]["active"].add(doctor.id)

    for visit in visits:
        doctor = visit.doctor
        doctor_visits[doctor.id].append(visit)
        day = visit.visit_date
        daily_counts[day]["visits"] += 1
        daily_counts[day]["doctors"].add(doctor.id)
        purpose_counts[visit.purpose or "(Not recorded)"] += 1
        outcome_counts[visit.outcome or "(Not recorded)"] += 1
        weekday_counts[day.strftime("%A")] += 1
        if visit.visit_time is None:
            time_counts["Time not recorded"] += 1
        elif visit.visit_time.hour < 9:
            time_counts["Before 9 AM"] += 1
        elif visit.visit_time.hour < 12:
            time_counts["9 AM–12 PM"] += 1
        elif visit.visit_time.hour < 15:
            time_counts["12 PM–3 PM"] += 1
        elif visit.visit_time.hour < 18:
            time_counts["3 PM–6 PM"] += 1
        else:
            time_counts["After 6 PM"] += 1
        area_name = doctor.area.name if doctor.area else "(No area)"
        hq_name = doctor.hq or "(No HQ)"
        area_groups[area_name]["visited"].add(doctor.id)
        area_groups[area_name]["visits"] += 1
        hq_groups[hq_name]["visited"].add(doctor.id)
        hq_groups[hq_name]["visits"] += 1
        if doctor.active:
            area_groups[area_name]["visited_active"].add(doctor.id)
            hq_groups[hq_name]["visited_active"].add(doctor.id)
        for product in visit.products:
            metric = product_counts.setdefault(product.id, {"id": product.id, "name": product.name,
                                                              "visits": 0, "doctors": set(), "areas": set()})
            metric["visits"] += 1
            metric["doctors"].add(doctor.id)
            if doctor.area:
                metric["areas"].add(doctor.area.name)
        if visit.follow_up_required:
            follow_up_counts["required"] += 1
            status = visit.follow_up_status.value if visit.follow_up_status else "pending"
            if status in {"pending", "completed", "cancelled"}:
                follow_up_counts[status] += 1
            if status == "pending" and visit.follow_up_date:
                if visit.follow_up_date < today:
                    follow_up_counts["overdue"] += 1
                elif visit.follow_up_date == today:
                    follow_up_counts["due_today"] += 1
                else:
                    follow_up_counts["upcoming"] += 1

    def territory_rows(groups):
        result = []
        for name, values in groups.items():
            population = len(values["active"])
            covered = len(values["visited"])
            active_covered = len(values["visited_active"])
            result.append({"name": name, "doctors": len(values["doctors"]), "active_doctors": population,
                           "visited_doctors": covered, "active_doctors_visited": active_covered,
                           "not_visited": max(population - active_covered, 0), "visits": values["visits"],
                           "coverage_percentage": round(active_covered * 100 / population, 1) if population else 0,
                           "average_visits_per_visited_doctor": round(values["visits"] / covered, 2) if covered else 0})
        return sorted(result, key=lambda row: (-row["visits"], row["name"]))

    doctor_rows = []
    frequency = {"not_visited": 0, "once": 0, "two_to_five": 0,
                 "six_to_ten": 0, "more_than_ten": 0}
    active_doctors = 0
    for doctor in doctors:
        calls = doctor_visits[doctor.id]
        count = len(calls)
        active_doctors += int(doctor.active)
        key = ("not_visited" if count == 0 else "once" if count == 1 else
               "two_to_five" if count <= 5 else "six_to_ten" if count <= 10 else "more_than_ten")
        if doctor.active:
            frequency[key] += 1
        call_dates = [visit.visit_date for visit in calls]
        gaps = [(later - earlier).days for earlier, later in zip(call_dates, call_dates[1:])]
        doctor_rows.append({
            "id": doctor.id, "name": doctor.name, "area": doctor.area.name if doctor.area else None,
            "hq": doctor.hq, "category": doctor.category, "specialty": doctor.specialty_group or doctor.existing_specialty,
            "active": doctor.active, "visits": count, "first_visit": call_dates[0] if call_dates else None,
            "last_visit": call_dates[-1] if call_dates else None,
            "days_since_last_visit": (today - call_dates[-1]).days if call_dates else None,
            "average_gap_days": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "longest_gap_days": max(gaps) if gaps else None,
            "products_discussed": len({product.id for visit in calls for product in visit.products}),
            "pending_follow_ups": sum(visit.follow_up_required and
                (visit.follow_up_status is None or visit.follow_up_status == FollowUpStatus.pending) for visit in calls),
        })
    doctor_rows.sort(key=lambda row: (row["visits"], row["name"].casefold()))

    doctor_by_id = {doctor.id: doctor for doctor in doctors}
    covered = len({visit.doctor_id for visit in visits})
    active_covered = len({visit.doctor_id for visit in visits if doctor_by_id[visit.doctor_id].active})
    total_dates = (end - start).days + 1
    return {
        "period": {"start": start, "end": end, "days": total_dates},
        "summary": {"visits": len(visits), "doctors_in_scope": len(doctors), "active_doctors": active_doctors,
                    "unique_doctors_visited": covered, "active_doctors_visited": active_covered,
                    "not_visited_active_doctors": max(active_doctors - active_covered, 0),
                    "coverage_percentage": round(active_covered * 100 / active_doctors, 1) if active_doctors else 0,
                    "calls_per_calendar_day": round(len(visits) / total_dates, 2) if total_dates else 0,
                    "calls_per_visited_doctor": round(len(visits) / covered, 2) if covered else 0},
        "frequency": frequency,
        "doctors": doctor_rows,
        "areas": territory_rows(area_groups),
        "hqs": territory_rows(hq_groups),
        "products": [{**metric, "doctors": len(metric["doctors"]), "areas": len(metric["areas"])} for metric in
                     sorted(product_counts.values(), key=lambda item: (-item["visits"], item["name"].casefold()))],
        "purposes": [{"name": name, "visits": count} for name, count in
                     sorted(purpose_counts.items(), key=lambda item: (-item[1], item[0].casefold()))],
        "outcomes": [{"name": name, "visits": count} for name, count in
                     sorted(outcome_counts.items(), key=lambda item: (-item[1], item[0].casefold()))],
        "trend": [{"date": day, "visits": metric["visits"], "doctors": len(metric["doctors"])}
                  for day, metric in sorted(daily_counts.items())],
        "weekday_distribution": [{"name": name, "visits": count} for name, count in weekday_counts.items()],
        "time_distribution": [{"name": name, "visits": count} for name, count in time_counts.items()],
        "follow_ups": follow_up_counts,
    }
