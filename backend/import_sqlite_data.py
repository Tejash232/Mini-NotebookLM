"""Explicit, repeatable import of the former app.sqlite3 into a migrated DB.

The SQLite source is opened read-only. This command never touches Chroma or PDF
files. Back up source SQLite, PDFs, and Chroma before any production import.
"""

import argparse
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from app import database


IMPORT_ORDER = ("users", "sessions", "login_attempts", "documents", "study_materials", "quizzes")


def _source_rows(source_path: Path) -> dict[str, list[dict]]:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {source_path}")
    read_only_uri = source_path.resolve().as_uri() + "?mode=ro"
    source = sqlite3.connect(read_only_uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        present = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(IMPORT_ORDER) - present
        if missing:
            raise ValueError(f"SQLite source is missing expected tables: {', '.join(sorted(missing))}")
        return {
            table_name: [dict(row) for row in source.execute(f'SELECT * FROM "{table_name}"')]
            for table_name in IMPORT_ORDER
        }
    finally:
        source.close()


def import_sqlite_database(source_path: str | Path, target_engine: Engine | None = None) -> dict[str, int]:
    """Copy source rows transactionally, safely skipping exact prior imports."""
    source_path = Path(source_path)
    rows_by_table = _source_rows(source_path)
    target_engine = target_engine or database.engine()
    if target_engine.dialect.name not in ("postgresql", "sqlite"):
        raise RuntimeError("SQLite import target must be PostgreSQL (SQLite is permitted only for isolated tests).")
    required_tables = set(IMPORT_ORDER)
    missing_tables = required_tables - set(inspect(target_engine).get_table_names())
    if missing_tables:
        raise RuntimeError("Target schema is not migrated. Run `alembic upgrade head` before importing.")

    tables = database.metadata.tables
    imported = {table_name: 0 for table_name in IMPORT_ORDER}
    with target_engine.begin() as target:
        for table_name in IMPORT_ORDER:
            table = tables[table_name]
            primary_key = [column.name for column in table.primary_key.columns]
            for row in rows_by_table[table_name]:
                where = [table.c[key] == row[key] for key in primary_key]
                prior = target.execute(select(table).where(*where)).mappings().first()
                if prior:
                    if any(prior[key] != value for key, value in row.items()):
                        raise ValueError(f"Target contains a conflicting {table_name} record; import stopped without overwriting it.")
                    continue

                dialect_insert = postgres_insert if target_engine.dialect.name == "postgresql" else sqlite_insert
                statement = dialect_insert(table).values(**row).on_conflict_do_nothing(index_elements=primary_key)
                result = target.execute(statement)
                if result.rowcount == 1:
                    imported[table_name] += 1
                    continue

                # A concurrent/repeated importer inserted this key. Verify it is
                # exactly the same source row; never silently overwrite it.
                prior = target.execute(select(table).where(*where)).mappings().first()
                if not prior or any(prior[key] != value for key, value in row.items()):
                    raise ValueError(f"Concurrent import produced a conflicting {table_name} record; import rolled back.")
        if target_engine.dialect.name == "postgresql":
            # Explicitly imported attempt IDs do not advance a PostgreSQL
            # serial sequence; synchronize it before new throttle rows arrive.
            target.execute(text("SELECT setval(pg_get_serial_sequence('login_attempts', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM login_attempts"))
    return imported


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=database.database_path(), help="Read-only source app.sqlite3 path")
    args = parser.parse_args()
    target_engine = database.engine()
    if target_engine.dialect.name != "postgresql":
        raise SystemExit("Refusing import: set DATABASE_URL to the intended PostgreSQL target.")
    if "alembic_version" not in inspect(target_engine).get_table_names():
        raise SystemExit("Refusing import: target is not Alembic-migrated. Run `alembic upgrade head` first.")
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    expected_revisions = set(ScriptDirectory.from_config(alembic_config).get_heads())
    with target_engine.connect() as connection:
        current_revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if current_revisions != expected_revisions:
        raise SystemExit("Refusing import: target schema is not at the current Alembic head.")
    counts = import_sqlite_database(args.source, target_engine)
    print("SQLite import completed (rows inserted by table):")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")
    print("Source SQLite, PDFs, and Chroma data were left unchanged.")


if __name__ == "__main__":
    main()
