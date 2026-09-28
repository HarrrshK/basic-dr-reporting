from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Area, Doctor, FollowUpStatus, Product, Visit
from app.services.sync_transfer import build_snapshot, replace_database_snapshot
from app.sync.queue import QueueRepository


def create_sqlite(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    QueueRepository(f"sqlite:///{path}").initialize()
    return engine


def test_snapshot_round_trip_preserves_relations_and_replaces_target(tmp_path):
    source = create_sqlite(tmp_path / "source.db")
    target = create_sqlite(tmp_path / "target.db")
    with Session(source) as db, db.begin():
        area = Area(id=4, name="Karjat")
        product = Product(id=8, name="Product A", description="Demo")
        doctor = Doctor(id=15, external_id="D-15", name="Dr Example", normalized_name="dr example",
                        area=area, active=True, extra_data={"custom": "preserved"})
        visit = Visit(id=21, doctor=doctor, visit_date=date(2026, 9, 28), purpose="Follow-up",
                      follow_up_required=True, follow_up_date=date(2026, 10, 2),
                      follow_up_status=FollowUpStatus.pending, products=[product])
        db.add_all([area, product, doctor, visit])

    with Session(source) as db:
        snapshot = build_snapshot(db)
    with Session(target) as db, db.begin():
        replace_database_snapshot(db, snapshot)
    with Session(target) as db:
        doctor = db.scalar(select(Doctor).where(Doctor.id == 15))
        visit = db.scalar(select(Visit).where(Visit.id == 21))
        assert doctor.area.name == "Karjat"
        assert doctor.extra_data == {"custom": "preserved"}
        assert visit.follow_up_status is FollowUpStatus.pending
        assert visit.products[0].name == "Product A"

    with Session(source) as db, db.begin():
        db.delete(db.get(Visit, 21))
        db.flush()
        db.delete(db.get(Doctor, 15))
    with Session(source) as db:
        updated_snapshot = build_snapshot(db)
    with Session(target) as db, db.begin():
        replace_database_snapshot(db, updated_snapshot)
    with Session(target) as db:
        assert db.scalar(select(Doctor.id)) is None
        assert db.scalar(select(Visit.id)) is None

    source.dispose()
    target.dispose()
