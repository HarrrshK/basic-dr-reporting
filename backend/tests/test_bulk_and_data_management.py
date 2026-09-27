from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.analytics import insights
from app.api.data_management import purge, summary
from app.api.doctors import create_doctor
from app.api.visits import create_bulk_visits, create_visit
from app.models import Doctor, Product, Visit, VisitProduct
from app.schemas import BulkVisitCreate, DoctorCreate, VisitCreate


def make_doctor(db, name, area="KHOPOLI", hq="BADLAPUR", category=None):
    return create_doctor(DoctorCreate(name=name, area=area, hq=hq, category=category), db)


def test_bulk_visit_creation_is_atomic_and_links_products(db):
    first = make_doctor(db, "Dr One")
    second = make_doctor(db, "Dr Two")
    product = Product(name="Shared Product")
    db.add(product); db.commit()
    result = create_bulk_visits(BulkVisitCreate(doctor_ids=[first.id, second.id], visit_date=date.today(),
        purpose="Camp", product_ids=[product.id]), db)
    assert result["created"] == 2
    assert db.scalar(select(func.count(Visit.id))) == 2
    assert db.scalar(select(func.count()).select_from(VisitProduct)) == 2

    missing = BulkVisitCreate(doctor_ids=[first.id, 999], visit_date=date.today())
    with pytest.raises(HTTPException):
        create_bulk_visits(missing, db)
    assert db.scalar(select(func.count(Visit.id))) == 2


def test_insights_compare_periods_and_rank_activity(db):
    first = make_doctor(db, "Dr Frequent", category="A")
    second = make_doctor(db, "Dr Unvisited")
    product = Product(name="Insight Product"); db.add(product); db.commit()
    today = date.today()
    create_visit(VisitCreate(doctor_id=first.id, visit_date=today, product_ids=[product.id]), db)
    create_visit(VisitCreate(doctor_id=first.id, visit_date=today - timedelta(days=1), product_ids=[product.id]), db)
    data = insights(start=today - timedelta(days=6), end=today, area="KHOPOLI", hq="BADLAPUR", category=None, db=db)
    assert data["kpis"]["visits"] == 2
    assert data["kpis"]["unique_doctors"] == 1
    assert data["kpis"]["repeat_visits"] == 1
    assert data["top_doctors"][0]["name"] == "Dr Frequent"
    assert data["top_products"][0]["visits"] == 2
    assert data["attention"][0]["name"] == "Dr Unvisited"


def test_category_purge_requires_phrase_and_respects_boundaries(db):
    doctor = make_doctor(db, "Dr Safe")
    product = Product(name="Keep Product"); db.add(product); db.commit()
    create_visit(VisitCreate(doctor_id=doctor.id, visit_date=date.today(), product_ids=[product.id],
        follow_up_required=True, follow_up_date=date.today() + timedelta(days=2)), db)
    counts = summary(db)
    assert counts["doctors"] == counts["visits"] == counts["products"] == counts["follow_ups"] == 1
    with pytest.raises(HTTPException):
        purge("visits", "wrong", db)
    purge("visits", "DELETE ALL VISITS", db)
    remaining = summary(db)
    assert remaining["visits"] == remaining["follow_ups"] == 0
    assert remaining["doctors"] == remaining["products"] == 1
