"""Lazy synchronous PostgreSQL configuration; never reads secret files."""

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import ArgumentError


def build_database_url() -> URL:
    supplied = os.environ.get("DATABASE_URL")
    if supplied:
        try:
            url = make_url(supplied)
        except (ArgumentError, ValueError, TypeError):
            # SQLAlchemy parsing errors may contain the original URL.
            raise ValueError("Invalid DATABASE_URL") from None
        if url.get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return url.set(drivername="postgresql+psycopg")
    required = ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("Missing database configuration: " + ", ".join(missing))
    try:
        port = int(os.environ.get("POSTGRES_PORT", "5432"))
    except ValueError:
        raise ValueError("POSTGRES_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("POSTGRES_PORT must be between 1 and 65535")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["POSTGRES_HOST"],
        port=port,
        database=os.environ["POSTGRES_DB"],
    )


def create_database_engine() -> Engine:
    """Call from adapters/CLI only; engine construction opens no connection."""
    return create_engine(build_database_url())
