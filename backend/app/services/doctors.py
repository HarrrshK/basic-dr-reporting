import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Area, Doctor


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return " ".join(text.split())


def normalize_mobile(value: object) -> str | None:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[-10:]
    return digits


def get_or_create_area(db: Session, name: str | None) -> Area | None:
    clean = " ".join((name or "").split()).strip()
    if not clean:
        return None
    area = db.scalar(select(Area).where(Area.name.ilike(clean)))
    if not area:
        area = Area(name=clean.upper())
        db.add(area)
        db.flush()
    return area


def apply_doctor_values(db: Session, doctor: Doctor, values: dict, *, nonblank_only: bool = False) -> Doctor:
    for field in (
        "external_id", "name", "existing_specialty", "hq", "category", "mobile", "active",
        "specialty_group", "doctor_status", "qualification", "gender", "clinic_hospital",
    ):
        if field not in values or (nonblank_only and values[field] in (None, "")):
            continue
        setattr(doctor, field, values[field])
    if values.get("name"):
        doctor.normalized_name = normalize_text(values["name"])
    if "mobile" in values and (not nonblank_only or values["mobile"]):
        doctor.normalized_mobile = normalize_mobile(values["mobile"])
    if values.get("area"):
        doctor.area = get_or_create_area(db, values["area"])
    if values.get("extra_data"):
        doctor.extra_data = {**(doctor.extra_data or {}), **values["extra_data"]}
    return doctor
