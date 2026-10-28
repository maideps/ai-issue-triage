"""Shared fixtures. Database tests need the docker-compose `db` service (or CI's service).

They skip when Postgres is unreachable, unless ``ISSUE_TRIAGE_REQUIRE_DB=1`` (set in CI
and in local feature gates), which turns an unreachable database into a failure.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.infrastructure.db.engine import make_engine, make_sessions, upgrade

TEST_DATABASE_URL = os.environ.get(
    "ISSUE_TRIAGE_TEST_DATABASE_URL",
    "postgresql+psycopg://triage:triage@localhost:5433/issue_triage_test",
)


def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        _create_database_if_missing(TEST_DATABASE_URL)
    except OperationalError as error:
        message = f"Postgres not reachable ({error.orig}); start it: docker compose up -d db"
        if os.environ.get("ISSUE_TRIAGE_REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(message)
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    upgrade(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def sessions(database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        tables = connection.scalars(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).all()
        if tables:
            connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield make_sessions(engine)
    engine.dispose()
