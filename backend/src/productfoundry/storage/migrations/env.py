from alembic import context

from productfoundry.storage.db import make_engine
from productfoundry.storage.models import Base

target_metadata = Base.metadata

# `storage.db.upgrade` passes its engine; the `alembic` command line builds one from settings.
engine = context.config.attributes.get("engine") or make_engine()

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
