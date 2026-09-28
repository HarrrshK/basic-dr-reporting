from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


database_url = settings.sqlalchemy_database_url
if database_url.startswith("sqlite"):
    engine = create_engine(database_url, pool_pre_ping=True,
                           connect_args={"check_same_thread": False})
else:
    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=300,
                           pool_size=3, max_overflow=2, pool_timeout=10,
                           connect_args={"connect_timeout": 10})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db
