from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


database_url = settings.permanent_database_url
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=300, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if database_url.startswith("sqlite"):
    @event.listens_for(SessionLocal, "before_commit")
    def record_sqlite_data_revision(session):
        session.connection().execute(text(
            "INSERT INTO sync_metadata (key, value) VALUES ('data_revision', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(sync_metadata.value AS INTEGER) + 1"
        ))


def get_db():
    with SessionLocal() as db:
        yield db
