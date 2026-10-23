"""PostgreSQL fixtures for integration tests against a disposable database.

Enable per service from its top-level conftest.py::

    pytest_plugins = ["test_support.postgres"]

Tests run only when BOTFLOW_TEST_DATABASE_URL names a disposable database,
e.g. postgresql+asyncpg://user:password@localhost:5432/test.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL_ENV = "BOTFLOW_TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{DATABASE_URL_ENV} is not set")
    return url


@pytest_asyncio.fixture
async def postgres_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()
