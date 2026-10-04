"""Engine, sessions and migrations."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from productfoundry.core.errors import ProductFoundryError
from productfoundry.settings import Settings

MIGRATIONS = Path(__file__).parent / "migrations"

Sessions = sessionmaker[Session]


class StorageUnavailable(ProductFoundryError):
    """The database cannot be reached or has not been migrated."""


def make_engine(url: URL | None = None, *, connect_timeout: int = 5) -> Engine:
    # Without a timeout a stopped database makes every command hang for minutes.
    return create_engine(
        url or Settings().sqlalchemy_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout},
    )


def make_sessions(engine: Engine) -> Sessions:
    return sessionmaker(engine, expire_on_commit=False)


def upgrade(engine: Engine, revision: str = "head") -> None:
    """Apply the Alembic migrations up to `revision`."""
    # Alembic is slow to import and only this function needs it.
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["engine"] = engine
    _connect(engine).close()
    command.upgrade(config, revision)


def check_ready(engine: Engine) -> None:
    """Raise `StorageUnavailable` with what to do when the database is not usable."""
    with _connect(engine) as connection:
        if not inspect(connection).has_table("runs"):
            raise StorageUnavailable(
                "the database has no tables yet; run `productfoundry db upgrade`"
            )


def _connect(engine: Engine):
    try:
        return engine.connect()
    except OperationalError as exc:
        where = engine.url.render_as_string(hide_password=True)
        raise StorageUnavailable(
            f"cannot reach the database at {where}; start it with `docker compose up -d db` "
            "or pass --memory"
        ) from exc
