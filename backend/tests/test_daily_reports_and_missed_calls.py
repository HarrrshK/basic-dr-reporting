from datetime import date

import pytest
from fastapi import HTTPException

from app.api.daily_reports import get_daily_report, missed_calls, save_daily_report
from app.api.doctors import create_doctor
from app.api.visits import create_visit
from app.models import DailyActivity
from app.schemas import DailyActivityCreate, DoctorCreate, VisitCreate


def test_daily_report_saves_optional_non_field_activity_and_updates_same_day(db):
    report_date = date(2026, 10, 11)
    saved = save_daily_report(report_date, DailyActivityCreate(
        report_date=report_date, activity_type="admin", description="  Monthly planning  "), db)
    assert saved["activity_type"] == "admin"
    assert saved["description"] == "Monthly planning"

    updated = save_daily_report(report_date, DailyActivityCreate(
        report_date=report_date, activity_type="leave", description=None), db)
    assert updated["id"] == saved["id"]
    assert updated["activity_type"] == "leave"
    assert db.query(DailyActivity).count() == 1


def test_daily_report_infers_field_work_from_existing_calls(db):
    doctor = create_doctor(DoctorCreate(name="Dr Today", area="KHOPOLI"), db)
    report_date = date(2026, 10, 11)
    create_visit(VisitCreate(doctor_id=doctor.id, visit_date=report_date, purpose="Routine call"), db)
    result = get_daily_report(report_date, db)
    assert result["activity"] is None
    assert result["inferred_activity_type"] == "field_work"
    assert result["call_count"] == result["unique_doctors"] == 1
    with pytest.raises(HTTPException, match="already marked") as duplicate:
        create_visit(VisitCreate(doctor_id=doctor.id, visit_date=report_date), db)
    assert duplicate.value.status_code == 409


def test_missed_calls_support_overall_hq_area_and_last_visit(db):
    called = create_doctor(DoctorCreate(name="Dr Called", area="KHOPOLI", hq="BADLAPUR"), db)
    missed = create_doctor(DoctorCreate(name="Dr Missed", area="KHOPOLI", hq="BADLAPUR"), db)
    never = create_doctor(DoctorCreate(name="Dr Never", area="KHOPOLI", hq="BADLAPUR"), db)
    other = create_doctor(DoctorCreate(name="Dr Other Area", area="KARJAT", hq="KARJAT"), db)
    create_visit(VisitCreate(doctor_id=called.id, visit_date=date(2026, 10, 5)), db)
    create_visit(VisitCreate(doctor_id=missed.id, visit_date=date(2026, 9, 15)), db)
    create_visit(VisitCreate(doctor_id=other.id, visit_date=date(2026, 9, 10)), db)

    result = missed_calls("2026-10", hq="BADLAPUR", area="KHOPOLI", db=db)
    assert result["active_doctors"] == 3
    assert result["covered_doctors"] == 1
    assert result["missed_calls"] == 2
    assert result["coverage_percentage"] == pytest.approx(33.3)
    assert [item["name"] for item in result["items"]] == ["Dr Never", "Dr Missed"]
    missed_row = next(item for item in result["items"] if item["id"] == missed.id)
    assert missed_row["last_visit"] == date(2026, 9, 15)
    assert missed_row["days_since_last_visit_at_month_end"] == 46


def test_missed_calls_rejects_invalid_month(db):
    with pytest.raises(HTTPException) as error:
        missed_calls("October", db=db)
    assert error.value.status_code == 422
