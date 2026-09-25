"""Brings the database up to the schema the code expects.

Runs when the backend starts (main.py) and can be run by hand:

    docker compose exec backend python -m models.migrate

  - empty database: applies schema.sql, which is always the CURRENT full schema, and
    records every file in migrations/ as applied because schema.sql already contains them;
  - existing database: applies each migrations/*.sql not yet recorded in
    schema_migrations, in filename order, one transaction per file;
  - some of the application tables but not all: refuses. Guessing here could overwrite
    somebody's data.

Migrations must be idempotent (ADD COLUMN IF NOT EXISTS ...): a database created before
schema_migrations existed already has the early ones applied, by hand, with no record.
Every new migration must also be folded into schema.sql.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_FILE = BACKEND_DIR / "schema.sql"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


class SchemaError(RuntimeError):
    pass


def tables_in(schema_sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)", schema_sql, re.IGNORECASE))


def plan(expected: set[str], existing: set[str], available: list[str], applied: set[str]) -> tuple[bool, list[str]]:
    """Returns (apply_schema, pending_migrations) for a database whose tables are `existing`."""
    if not existing & expected:
        return True, []
    missing = expected - existing
    if missing:
        raise SchemaError(
            f"the database has some of the application tables but not {sorted(missing)}. "
            "Refusing to guess. For a development database with nothing worth keeping, empty it "
            "(see 'Database' in the README) and start again; otherwise repair it by hand."
        )
    return False, [v for v in available if v not in applied]


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def run(dsn: str | None = None) -> tuple[bool, list[str]]:
    """Returns (created_schema, migrations_applied)."""
    dsn = dsn or os.getenv("DATABASE_URL")
    if not dsn:
        raise SchemaError("DATABASE_URL is not set")

    schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
    files = _migration_files()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT NOW())"
        )
        existing = {r["tablename"] for r in await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )}
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}

        apply_schema, pending = plan(tables_in(schema_sql), existing, [f.stem for f in files], applied)

        if apply_schema:
            async with conn.transaction():
                await conn.execute(schema_sql)
                for f in files:
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING", f.stem
                    )
            return True, []

        done = []
        for f in files:
            if f.stem not in pending:
                continue
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", f.stem)
            done.append(f.stem)
        return False, done
    finally:
        await conn.close()


def describe(created: bool, applied: list[str]) -> str:
    if created:
        return "database: empty, created the schema from schema.sql"
    if applied:
        return "database: applied migrations " + ", ".join(applied)
    return "database: schema is up to date"


if __name__ == "__main__":
    try:
        print(describe(*asyncio.run(run())))
    except SchemaError as e:
        sys.exit(f"Database setup failed: {e}")
