from datetime import date, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import FollowUpStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AreaOut(ORMModel):
    id: int
    name: str


class DoctorBase(BaseModel):
    external_id: str | None = None
    name: str = Field(min_length=1, max_length=240)
    existing_specialty: str | None = None
    area: str | None = None
    hq: str | None = None
    category: str | None = None
    mobile: str | None = None
    active: bool = True
    specialty_group: str | None = None
    doctor_status: str | None = None
    qualification: str | None = None
    gender: str | None = None
    clinic_hospital: str | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    external_id: str | None = None
    name: str | None = None
    existing_specialty: str | None = None
    area: str | None = None
    hq: str | None = None
    category: str | None = None
    mobile: str | None = None
    active: bool | None = None
    specialty_group: str | None = None
    doctor_status: str | None = None
    qualification: str | None = None
    gender: str | None = None
    clinic_hospital: str | None = None
    extra_data: dict[str, Any] | None = None


class DoctorOut(ORMModel):
    id: int
    external_id: str | None
    name: str
    existing_specialty: str | None
    area: AreaOut | None
    hq: str | None
    category: str | None
    mobile: str | None
    active: bool
    specialty_group: str | None
    doctor_status: str | None
    qualification: str | None
    gender: str | None
    clinic_hospital: str | None
    extra_data: dict[str, Any]


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProductOut(ORMModel):
    id: int
    name: str
    description: str | None
    active: bool


class VisitCreate(BaseModel):
    doctor_id: int
    visit_date: date
    visit_time: time | None = None
    purpose: str | None = None
    product_ids: list[int] = Field(default_factory=list)
    outcome: str | None = None
    notes: str | None = None
    follow_up_required: bool = False
    follow_up_date: date | None = None
    follow_up_reason: str | None = None

    @model_validator(mode="after")
    def validate_follow_up(self):
        if self.follow_up_required and not self.follow_up_date:
            raise ValueError("follow_up_date is required when follow-up is required")
        return self


class VisitOut(ORMModel):
    id: int
    doctor_id: int
    visit_date: date
    visit_time: time | None
    purpose: str | None
    outcome: str | None
    notes: str | None
    follow_up_required: bool
    follow_up_date: date | None
    follow_up_reason: str | None
    follow_up_status: FollowUpStatus | None
    products: list[ProductOut]


class ImportConfirm(BaseModel):
    mapping: dict[str, str] | None = None
    resolutions: dict[str, str] = Field(default_factory=dict, description="row index to create or doctor id")
