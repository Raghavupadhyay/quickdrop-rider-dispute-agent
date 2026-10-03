import os
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/quickdrop",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


def init_db(retries: int = 30, delay: float = 1.0) -> None:
    """Create tables, waiting for Postgres to accept connections first."""
    from app.db import models  # noqa: F401  (registers the tables on Base)

    last_error: Exception | None = None

    for _ in range(retries):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception as exc:  # pragma: no cover - only hit while Postgres boots
            last_error = exc
            time.sleep(delay)

    raise RuntimeError(f"Database not reachable: {last_error}")


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
