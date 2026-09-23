"""SQLAlchemy engine/session plumbing for tier 2 (PostgreSQL in production, SQLite in tests)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


def make_session_factory(engine: Engine) -> Callable[[], Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all(engine: Engine) -> None:
    """Dev/test convenience. Production schema changes go through Alembic (infra/migrations)."""
    from packages.orchestrator.memory import persistent  # noqa: F401 — registers the models

    Base.metadata.create_all(engine)
