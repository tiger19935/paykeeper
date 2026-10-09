"""Integration test fixtures.

A single Postgres container runs for the whole pytest session. Each test
truncates every table through a fast per-test fixture. If Docker is not
available, integration tests are skipped — unit tests still run.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TESTCONTAINERS = True
except Exception:
    _HAS_TESTCONTAINERS = False


_PG_IMAGE = "postgres:16-alpine"


def _docker_available() -> bool:
    if not _HAS_TESTCONTAINERS:
        return False
    import shutil

    if shutil.which("docker") is None:
        return False
    import subprocess

    try:
        subprocess.run(
            ["docker", "info"],  # noqa: S607
            check=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    if (dsn := os.environ.get("PAYKEEPER_TEST_DATABASE_URL")) is not None:
        yield dsn
        return
    if not _docker_available():
        pytest.skip("Docker is not available; integration tests skipped")

    with PostgresContainer(_PG_IMAGE, driver="asyncpg") as pg:
        raw = pg.get_connection_url()
        if raw.startswith("postgresql+asyncpg://"):
            yield raw
        else:
            yield raw.replace("postgresql://", "postgresql+asyncpg://", 1)


def _run_migrations(dsn: str) -> None:
    import subprocess
    import sys

    env = os.environ.copy()
    env["PAYKEEPER_DATABASE_URL"] = dsn
    env.setdefault("PAYKEEPER_WEBHOOK_SECRET", "test-secret")
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade failed: rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest_asyncio.fixture(scope="session")
async def engine(postgres_dsn: str) -> AsyncIterator[AsyncEngine]:
    from paykeeper.db.session import create_engine

    _run_migrations(postgres_dsn)
    eng = create_engine(postgres_dsn)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE outbox_events, webhook_events, idempotency_keys, "
                "ledger_entries, refunds, charges RESTART IDENTITY CASCADE"
            )
        )
    async with sm() as session:
        yield session


@pytest_asyncio.fixture
async def sessionmaker_(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
