from io import BytesIO
from pathlib import Path
from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import Doctor
from app.services.imports import confirm_import, preview_import


def workbook(rows):
    book=Workbook(); sheet=book.active
    for row in rows: sheet.append(row)
    output=BytesIO(); book.save(output); return output.getvalue()


def test_import_preview_confirm_and_repeat_is_idempotent(db):
    content=workbook([["Doctor Name","Area","Mobile Number","Unknown Rating"],["Dr A","KHOPOLI","+91 98765 43210","Gold"]])
    preview=preview_import(db,"doctors.xlsx",content)
    assert preview.summary == {"new":1,"matched":0,"ambiguous":0,"skipped":0,"errors":0,"total":1,"unmapped_columns":["Unknown Rating"]}
    assert confirm_import(db,preview,None,{})["new"] == 1
    second=preview_import(db,"doctors.xlsx",content)
    assert second.summary["matched"] == 1
    result=confirm_import(db,second,None,{})
    assert result["updated"] == 1
    assert db.scalar(select(func.count(Doctor.id))) == 1
    assert db.scalar(select(Doctor)).extra_data["Unknown Rating"] == "Gold"


def test_import_validation_does_not_silently_create_bad_rows(db):
    content=workbook([["Doctor Name","Active"],[None,"Maybe"]])
    preview=preview_import(db,"bad.xlsx",content)
    assert preview.summary["errors"] == 1
    result=confirm_import(db,preview,None,{})
    assert result["errors"] == 1
    assert db.scalar(select(func.count(Doctor.id))) == 0


def test_real_master_format_uses_doctor_id_and_keeps_shared_mobile_distinct(db):
    content = (Path(__file__).parents[2] / "dr.xlsx").read_bytes()
    preview = preview_import(db, "dr.xlsx", content)
    assert preview.summary["total"] == 250
    assert preview.mapping["Doctor ID"] == "external_id"
    assert not any(str(column).startswith("Unnamed") for column in preview.summary["unmapped_columns"])
    result = confirm_import(db, preview, None, {})
    assert result["new"] == 133
    assert result["skipped"] == 117
    shared = list(db.scalars(select(Doctor).where(Doctor.normalized_mobile == "9028291781")))
    assert len(shared) == 2
    assert {doctor.external_id for doctor in shared} == {"DR0001", "DR0002"}
    repeated = preview_import(db, "dr.xlsx", content)
    assert repeated.summary["matched"] == 133
    assert repeated.summary["skipped"] == 117
