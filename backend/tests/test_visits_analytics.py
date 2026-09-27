from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.analytics import areas, coverage, dashboard
from app.api.doctors import clear_doctor_master, create_doctor, doctor_profile, filter_options, remove_doctor
from app.api.visits import create_visit, list_visits
from app.models import Product
from app.schemas import DoctorCreate, VisitCreate


def doctor(db, name="Dr A", area="KHOPOLI"):
    return create_doctor(DoctorCreate(name=name, area=area), db)


def test_visit_relationship_products_and_dashboard_counts(db):
    first = doctor(db)
    doctor(db, "Dr B")
    product = Product(name="Product One")
    db.add(product)
    db.commit()
    visit = create_visit(VisitCreate(doctor_id=first.id, visit_date=date.today(), product_ids=[product.id], purpose="Detailing"), db)
    assert visit.products[0].name == "Product One"
    stats = dashboard(db=db)
    assert stats["total_doctors"] == 2
    assert stats["total_visits"] == 1
    assert stats["visited_doctors_in_period"] == 1
    assert stats["coverage_percentage"] == 50


def test_profile_intervals_and_area_coverage(db):
    record = doctor(db)
    today = date.today()
    for days in (20, 10, 0):
        create_visit(VisitCreate(doctor_id=record.id, visit_date=today - timedelta(days=days)), db)
    profile = doctor_profile(record.id, db)
    assert profile["metrics"]["total_visits"] == 3
    assert profile["metrics"]["average_visit_interval"] == 10
    assert profile["metrics"]["longest_gap"] == 10
    results = areas(start=today - timedelta(days=30), end=today, db=db)
    assert results[0]["visits"] == 3
    assert results[0]["coverage_percentage"] == 100


def test_visit_rejects_missing_doctor_and_invalid_followup(db):
    with pytest.raises(HTTPException):
        create_visit(VisitCreate(doctor_id=999, visit_date=date.today()), db)
    record = doctor(db)
    with pytest.raises(ValueError):
        VisitCreate(doctor_id=record.id, visit_date=date.today(), follow_up_required=True)


def test_doctor_removal_preserves_visit_history_and_bulk_clear_is_safe(db):
    visited = doctor(db)
    unvisited = doctor(db, "Dr B")
    create_visit(VisitCreate(doctor_id=visited.id, visit_date=date.today()), db)
    assert remove_doctor(visited.id, db)["action"] == "deactivated"
    assert db.get(type(visited), visited.id).active is False
    result = clear_doctor_master("DELETE ALL DOCTORS", db)
    assert result == {"deleted": 1, "deactivated": 1,
                      "message": "Doctors with visit history were deactivated so historical visits remain valid."}
    assert db.get(type(unvisited), unvisited.id) is None
    assert db.get(type(visited), visited.id) is not None


def test_dependent_territory_filters_scope_doctors_visits_and_coverage(db):
    khopoli = create_doctor(DoctorCreate(name="Dr Khopoli", area="KHOPOLI", hq="BADLAPUR"), db)
    karjat = create_doctor(DoctorCreate(name="Dr Karjat", area="KARJAT", hq="PANVEL"), db)
    create_visit(VisitCreate(doctor_id=khopoli.id, visit_date=date.today(), purpose="Detailing"), db)
    create_visit(VisitCreate(doctor_id=karjat.id, visit_date=date.today(), purpose="Follow-up"), db)

    options = filter_options(hq="BADLAPUR", area=None, active=True, db=db)
    assert [item["name"] for item in options["areas"]] == ["KHOPOLI"]
    assert [item["name"] for item in options["doctors"]] == ["Dr Khopoli"]

    visits = list_visits(date_from=None, date_to=None, doctor_id=None, area="KHOPOLI", hq="BADLAPUR",
        category=None, product_id=None, purpose=None, outcome=None, follow_up_required=None, q=None,
        page=1, page_size=50, db=db)
    assert visits["total"] == 1
    assert visits["items"][0].doctor_id == khopoli.id

    scoped = coverage(area="KHOPOLI", hq="BADLAPUR", category=None, db=db)
    assert scoped["active_doctors"] == scoped["visited"] == 1
    assert scoped["coverage_percentage"] == 100
