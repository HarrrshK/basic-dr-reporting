from datetime import date, time

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.api.backup import BackupImport, archive_backup, backup_status, request_backup
from app.config import settings
from app.models import Area, BackupAgentState, Doctor, FollowUpStatus, ImportBatch, Product, Visit, VisitProduct
from app.services.backup_archive import apply_backup_changes, make_backup_document, restore_backup_document
from app.schemas import VisitCreate
from app.api.visits import create_visit
from scripts.laptop_backup_agent import LaptopBackupAgent
from scripts.migrate_legacy_sqlite import migrate


def make_engine(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return engine


def seed(session):
    area = Area(id=10, name="Karjat")
    product = Product(id=20, name="Product A", description="Example")
    doctor = Doctor(id=30, external_id="D-30", name="Dr Example", normalized_name="dr example",
                    area=area, active=True, extra_data={"source": "workbook"})
    visit = Visit(id=40, doctor=doctor, visit_date=date(2026, 9, 28), visit_time=time(10, 30),
                  purpose="Follow-up", follow_up_required=True, follow_up_date=date(2026, 10, 2),
                  follow_up_status=FollowUpStatus.pending, products=[product])
    batch = ImportBatch(id="batch-1", filename="doctors.xlsx", file_hash="a" * 64,
                        mapping={"doctor": "Doctor Name"}, rows=[], status="complete", summary={"new": 1})
    session.add_all([area, product, doctor, visit, batch])


def test_json_backup_restore_is_repeat_safe_and_keeps_relationships(db, tmp_path):
    seed(db)
    db.commit()
    document = make_backup_document(db, date(2026, 9, 28))
    target = make_engine(tmp_path / "restore.db")
    TargetSession = sessionmaker(target, expire_on_commit=False)
    for _ in range(2):
        with TargetSession.begin() as target_db:
            result = restore_backup_document(target_db, document)
        assert result["doctors"] == 1
    with TargetSession() as target_db:
        assert target_db.scalar(select(func.count(Doctor.id))) == 1
        doctor = target_db.get(Doctor, 30)
        visit = target_db.get(Visit, 40)
        assert doctor.area.name == "Karjat"
        assert doctor.extra_data == {"source": "workbook"}
        assert visit.visit_date == date(2026, 9, 28)
        assert visit.follow_up_status is FollowUpStatus.pending
        assert visit.products[0].id == 20
        assert target_db.get(ImportBatch, "batch-1").summary == {"new": 1}
    target.dispose()


def test_corrupt_json_is_rejected_before_any_rows_are_written(db, tmp_path):
    seed(db)
    db.commit()
    document = make_backup_document(db)
    document["records"]["doctors"][0]["unknown_field"] = "must reject"
    target = make_engine(tmp_path / "invalid.db")
    TargetSession = sessionmaker(target, expire_on_commit=False)
    with pytest.raises(ValueError, match="missing or unknown fields"):
        with TargetSession.begin() as target_db:
            restore_backup_document(target_db, document)
    with TargetSession() as target_db:
        assert target_db.scalar(select(func.count(Area.id))) == 0
    target.dispose()


def test_invalid_relationship_rolls_back_json_restore(tmp_path):
    target = make_engine(tmp_path / "rollback.db")
    event.listen(target, "connect", lambda connection, record: connection.execute("PRAGMA foreign_keys=ON"))
    target.dispose()
    TargetSession = sessionmaker(target, expire_on_commit=False)
    document = {
        "export_version": 1, "export_date": "2026-09-28", "generated_at": "2026-09-28T00:00:00+00:00",
        "records": {model.__tablename__: [] for model in (Area, Product, ImportBatch, Doctor, Visit, VisitProduct)},
    }
    document["records"]["doctors"] = [{
        "id": 1, "external_id": None, "name": "Dr Missing Area", "normalized_name": "dr missing area",
        "existing_specialty": None, "area_id": 999, "hq": None, "category": None, "mobile": None,
        "normalized_mobile": None, "active": True, "specialty_group": None, "doctor_status": None,
        "qualification": None, "gender": None, "clinic_hospital": None, "extra_data": {},
        "created_at": "2026-09-28T00:00:00", "updated_at": "2026-09-28T00:00:00",
    }]
    with pytest.raises(IntegrityError):
        with TargetSession.begin() as target_db:
            restore_backup_document(target_db, document)
    with TargetSession() as target_db:
        assert target_db.scalar(select(func.count(Doctor.id))) == 0
    target.dispose()


def test_incremental_upserts_and_deletes_are_idempotent(db):
    changes = [
        {"table_name": "areas", "record_id": "5", "operation": "upsert",
         "row_data": {"id": 5, "name": "Badlapur"}},
        {"table_name": "doctors", "record_id": "7", "operation": "upsert",
         "row_data": {"id": 7, "external_id": None, "name": "Dr Test", "normalized_name": "dr test",
                       "existing_specialty": None, "area_id": 5, "hq": None, "category": None,
                       "mobile": None, "normalized_mobile": None, "active": True, "specialty_group": None,
                       "doctor_status": None, "qualification": None, "gender": None, "clinic_hospital": None,
                       "extra_data": {}, "created_at": "2026-09-28T00:00:00", "updated_at": "2026-09-28T00:00:00"}},
    ]
    for _ in range(2):
        apply_backup_changes(db, changes)
        db.commit()
    assert db.scalar(select(func.count(Doctor.id))) == 1
    update = {**changes[1], "row_data": {**changes[1]["row_data"], "name": "Dr Updated"}}
    apply_backup_changes(db, [update]); db.commit()
    assert db.get(Doctor, 7).name == "Dr Updated"
    deletion = {"table_name": "doctors", "record_id": "7", "operation": "delete", "row_data": None}
    apply_backup_changes(db, [deletion, deletion]); db.commit()
    assert db.get(Doctor, 7) is None


def test_application_writes_and_reads_work_with_laptop_offline(db):
    area = Area(name="Karjat")
    doctor = Doctor(name="Dr Offline", normalized_name="dr offline", active=True, area=area)
    db.add(doctor); db.commit()
    offline = backup_status(db)
    assert offline["laptop_connected"] is False
    created = create_visit(VisitCreate(doctor_id=doctor.id, visit_date=date(2026, 9, 28), purpose="Routine"), db)
    assert created.doctor_id == doctor.id
    assert db.scalar(select(func.count(Visit.id))) == 1
    request_backup(db)
    assert db.get(BackupAgentState, 1).requested_at is not None


def test_laptop_agent_commits_changes_and_cursor_in_one_transaction(tmp_path):
    target = make_engine(tmp_path / "laptop.db")
    agent = object.__new__(LaptopBackupAgent)
    agent.engine = target
    changes = [{"table_name": "areas", "record_id": "55", "operation": "upsert",
                "row_data": {"id": 55, "name": "Karjat"}}]
    agent.apply_changes(changes, 18)
    agent.apply_changes(changes, 18)
    with Session(target) as laptop:
        assert laptop.get(Area, 55).name == "Karjat"
        state = laptop.get(BackupAgentState, 1)
        assert state.cursor == 18 and state.bootstrap_complete
    target.dispose()


def test_external_json_archive_confirms_only_after_object_storage_upload(db, monkeypatch):
    monkeypatch.setattr(settings, "backup_provider", "s3")
    monkeypatch.setattr(settings, "backup_bucket", "reports-test")
    monkeypatch.setattr(settings, "aws_access_key_id", "key")
    monkeypatch.setattr(settings, "aws_secret_access_key", "secret")
    monkeypatch.setattr(settings, "aws_region", "test-region")
    captured = {}

    class Client:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Client())
    document = make_backup_document(db, date(2026, 9, 28))
    result = archive_backup(BackupImport(document=document), db)
    assert result["uploaded"] is True
    assert captured["Bucket"] == "reports-test"
    assert captured["Key"].endswith("2026-09-28.json")
    assert db.get(BackupAgentState, 1).last_external_backup_at is not None


def test_sqlite_migration_preserves_ids_counts_and_is_repeat_safe(tmp_path):
    source = make_engine(tmp_path / "old.sqlite")
    target = make_engine(tmp_path / "new.sqlite")
    with Session(source) as source_db, source_db.begin():
        seed(source_db)
    result = migrate(tmp_path / "old.sqlite", str(target.url))
    assert result["doctors"]["source_rows"] == 1
    assert result["doctors"]["inserted"] == 1
    assert result["relationship_checks"] == {
        "visits_without_doctor": 0, "visit_products_without_visit": 0, "visit_products_without_product": 0,
    }
    repeated = migrate(tmp_path / "old.sqlite", str(target.url))
    assert repeated["doctors"]["inserted"] == 0
    assert repeated["doctors"]["already_present"] == 1
    with Session(target) as target_db:
        assert target_db.get(Doctor, 30).area.name == "Karjat"
        assert target_db.get(Visit, 40).products[0].id == 20
    source.dispose(); target.dispose()


def test_sqlite_migration_rejects_conflicting_existing_id_atomically(tmp_path):
    source = make_engine(tmp_path / "source-conflict.sqlite")
    target = make_engine(tmp_path / "target-conflict.sqlite")
    with Session(source) as source_db, source_db.begin():
        source_db.add(Area(id=1, name="Source Area"))
    with Session(target) as target_db, target_db.begin():
        target_db.add(Area(id=1, name="Other Area"))
    with pytest.raises(ValueError, match="Conflicting existing areas ID"):
        migrate(tmp_path / "source-conflict.sqlite", str(target.url))
    with Session(target) as target_db:
        assert target_db.scalar(select(func.count(Area.id))) == 1
        assert target_db.get(Area, 1).name == "Other Area"
    source.dispose(); target.dispose()
