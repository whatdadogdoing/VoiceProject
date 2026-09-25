"""Tests for models/migrate.py, the database setup that runs when the backend starts.

The pure logic is tested directly. The tests that run SQL need a real Postgres and run
only when MIGRATION_TEST_DSN is set. They empty the whole public schema of that
database before each test, so the database name must end in "_test": pointing this at
the real one raises instead of wiping it.

    docker compose exec postgres psql -U user -d voiceauth -c "CREATE DATABASE voiceauth_test"
    MIGRATION_TEST_DSN=postgresql://user:password@postgres/voiceauth_test pytest tests/test_migrate.py
"""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

from models import migrate
from models.migrate import MIGRATIONS_DIR, SCHEMA_FILE, SchemaError, plan, tables_in

OLD_SCHEMA = Path(__file__).parent / "fixtures" / "schema_before_model_id.sql"
TABLES = {"users", "voiceprints", "voice_auth_attempts", "consent_records"}


def test_the_expected_tables_are_read_from_schema_sql():
    assert tables_in(SCHEMA_FILE.read_text(encoding="utf-8")) == TABLES


def test_an_empty_database_gets_the_full_schema():
    assert plan(TABLES, set(), ["001_a"], set()) == (True, [])


def test_unrelated_tables_do_not_count_as_an_existing_installation():
    assert plan(TABLES, {"schema_migrations", "something_else"}, ["001_a"], set()) == (True, [])


def test_an_existing_database_gets_only_the_migrations_it_lacks():
    assert plan(TABLES, TABLES, ["001_a", "002_b", "003_c"], {"001_a"}) == (False, ["002_b", "003_c"])


def test_an_up_to_date_database_gets_nothing():
    assert plan(TABLES, TABLES, ["001_a"], {"001_a"}) == (False, [])


def test_a_half_initialised_database_is_refused_not_guessed_at():
    with pytest.raises(SchemaError, match="voiceprints"):
        plan(TABLES, {"users", "consent_records", "voice_auth_attempts"}, [], set())


async def test_missing_database_url_is_reported_plainly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SchemaError, match="DATABASE_URL"):
        await migrate.run()


def test_migration_files_are_numbered_uniquely_and_in_order():
    names = [f.name for f in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert names, "no migrations found"
    assert all(re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", n) for n in names), names
    numbers = [n[:3] for n in names]
    assert len(set(numbers)) == len(numbers)


def test_every_migration_is_idempotent():
    # A database created before schema_migrations existed has the early migrations
    # applied already, by hand, so the runner must be able to apply them again.
    for f in MIGRATIONS_DIR.glob("*.sql"):
        sql = f.read_text(encoding="utf-8")
        assert not re.search(r"ADD COLUMN(?!\s+IF NOT EXISTS)", sql, re.IGNORECASE), f.name
        assert not re.search(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX)(?!\s+IF NOT EXISTS)", sql, re.IGNORECASE), f.name


# -- against a real database ---------------------------------------------------------

DSN = os.getenv("MIGRATION_TEST_DSN")
needs_postgres = pytest.mark.skipif(not DSN, reason="set MIGRATION_TEST_DSN to a throwaway Postgres database")


@pytest.fixture
async def conn():
    assert urlparse(DSN).path.lstrip("/").endswith("_test"), "MIGRATION_TEST_DSN must name a database ending in _test"
    c = await asyncpg.connect(DSN)
    await _empty(c)
    yield c
    await c.close()


async def _empty(c):
    await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")


async def _signature(c):
    columns = await c.fetch(
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
    )
    indexes = await c.fetch("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1")
    return [tuple(r) for r in columns], [tuple(r) for r in indexes]


def _all_migrations():
    return [f.stem for f in sorted(MIGRATIONS_DIR.glob("*.sql"))]


async def _recorded(c):
    return [r["version"] for r in await c.fetch("SELECT version FROM schema_migrations ORDER BY version")]


@needs_postgres
async def test_an_empty_database_is_built_and_every_migration_is_recorded(conn):
    assert await migrate.run(DSN) == (True, [])

    tables = {r["tablename"] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert TABLES <= tables
    assert await _recorded(conn) == _all_migrations()


@needs_postgres
async def test_running_it_again_changes_nothing(conn):
    await migrate.run(DSN)
    before = await _signature(conn)

    assert await migrate.run(DSN) == (False, [])
    assert await _signature(conn) == before


@needs_postgres
async def test_a_database_from_before_schema_migrations_existed_is_brought_up_to_date(conn):
    # What a volume created by `docker compose up` under the old setup looks like:
    # tables from schema.sql, no schema_migrations table.
    await conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

    assert await migrate.run(DSN) == (False, _all_migrations())
    assert await migrate.run(DSN) == (False, [])


@needs_postgres
async def test_an_old_database_is_upgraded_to_exactly_what_a_new_one_gets(conn):
    await migrate.run(DSN)
    fresh = await _signature(conn)

    await _empty(conn)
    await conn.execute(OLD_SCHEMA.read_text(encoding="utf-8"))
    user_id = await conn.fetchval("INSERT INTO users (email, password_hash) VALUES ('a@example.com', 'x') RETURNING id")
    await conn.execute("INSERT INTO voiceprints (user_id, embedding) VALUES ($1, $2)", user_id, [0.0] * 256)

    assert await migrate.run(DSN) == (False, _all_migrations())

    assert await _signature(conn) == fresh
    row = await conn.fetchrow("SELECT model_id, embedding_dim, is_active FROM voiceprints")
    assert (row["model_id"], row["embedding_dim"], row["is_active"]) == ("resemblyzer-0.1.4", 256, True)


@needs_postgres
async def test_a_half_built_database_is_left_alone(conn):
    await conn.execute("CREATE TABLE users (id UUID PRIMARY KEY)")

    with pytest.raises(SchemaError):
        await migrate.run(DSN)

    tables = {r["tablename"] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert "voiceprints" not in tables
