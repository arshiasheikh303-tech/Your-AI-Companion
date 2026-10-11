"""Engine/session management. Swap DATABASE_URL to move to PostgreSQL later."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import BASE_DIR, get_settings
from app.database.models import Base

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        # make relative sqlite paths relative to the project, not the CWD
        if url.startswith("sqlite:///./"):
            rel = url.replace("sqlite:///./", "")
            path = (BASE_DIR / rel).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_engine(url: str | None = None) -> Engine:
    """(Re)initialise the global engine. Tests pass an in-memory/temp URL."""
    global _engine, _SessionLocal
    _engine = _make_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_migrations() -> None:
    """Apply Alembic migrations; fall back to create_all if Alembic is unavailable."""
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(BASE_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
        cfg.set_main_option("sqlalchemy.url", get_engine().url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(cfg, "head")
    except Exception:
        log.exception("alembic upgrade failed; falling back to create_all")
        Base.metadata.create_all(get_engine())


def check_database() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("database check failed")
        return False
