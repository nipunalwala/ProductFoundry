import os

# Tests never download a model: the embedding tests skip when it is not cached.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import pytest

from productfoundry.core.run_input import RunInput


@pytest.fixture(autouse=True)
def no_live_llm(monkeypatch):
    """Tests never reach an LLM provider, whatever keys `.env` holds."""
    monkeypatch.setattr("productfoundry.llm.live_providers", lambda routing, settings: {})


def run_input_data(**overrides) -> dict:
    data = {
        "mode": "alternative",
        "idea": "A simpler UPI expense tracker",
        "target_users": "Salaried people in Indian metros",
        "platforms": ["android", "ios"],
        "region": "IN",
        "incumbent": {
            "name": "Walnut",
            "urls": ["https://walnut.example"],
            "store_ids": {"google_play": "com.example.walnut", "app_store": "123456789"},
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def run_input() -> RunInput:
    return RunInput.model_validate(run_input_data())


# Database fixtures. They use a separate `<name>_test` database on the Docker server,
# so tests never touch the data of real runs.

DB_SKIP = "database tests skipped: the Docker database is not running (docker compose up -d db)"


@pytest.fixture(scope="session")
def db_engine():
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    from productfoundry.settings import Settings
    from productfoundry.storage import make_engine, upgrade

    admin_url = Settings().sqlalchemy_url()
    test_url = admin_url.set(database=f"{admin_url.database}_test")
    admin = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": test_url.database}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except OperationalError:
        pytest.skip(DB_SKIP)
    finally:
        admin.dispose()

    engine = make_engine(test_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    upgrade(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(db_engine):
    from sqlalchemy import text

    from productfoundry.storage import make_sessions
    from productfoundry.storage.models import Base

    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with db_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return make_sessions(db_engine)
