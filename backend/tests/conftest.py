"""Shared isolated PostgreSQL fixtures for persistence and later adapter tests."""

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def database_url():
    """Create only a uniquely named database on an explicitly isolated test service."""
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        pytest.skip("TEST_DATABASE_URL absent; isolated PostgreSQL integration not run")
    url = make_url(configured).set(drivername="postgresql+psycopg")
    if not url.database or not url.database.startswith("test_"):
        pytest.fail("TEST_DATABASE_URL must name a test_-prefixed isolated database")
    name = f"test_{uuid4().hex}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        yield url.set(database=name)
    finally:
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def database_environment(database_url, monkeypatch):
    """Environment inherited by application CLI subprocesses, never the live DB."""
    monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
    for key, value in {
        "POSTGRES_HOST": database_url.host,
        "POSTGRES_PORT": database_url.port or 5432,
        "POSTGRES_DB": database_url.database,
        "POSTGRES_USER": database_url.username,
        "POSTGRES_PASSWORD": database_url.password,
    }.items():
        monkeypatch.setenv(key, str(value or ""))
    return os.environ.copy()


@pytest.fixture
def alembic_cli(database_environment):
    def run(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), *arguments],
            cwd=BACKEND.parent,
            env=database_environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Do not surface driver errors that could contain connection credentials.
        assert result.returncode == 0, "Alembic CLI failed (connection details withheld)"
        return result

    return run


@pytest.fixture
def migrated_engine(database_url, alembic_cli):
    alembic_cli("upgrade", "head")
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        yield engine
    finally:
        engine.dispose()
