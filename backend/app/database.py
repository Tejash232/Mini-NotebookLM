"""SQLAlchemy persistence for accounts, sessions, documents, and study work.

Schema changes are managed by Alembic. Tables are created automatically only in
explicit APP_ENV=test runs so tests can use isolated SQLite databases.
"""

import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    JSON, Column, Float, ForeignKey, ForeignKeyConstraint, Index, Integer,
    MetaData, String, Table, Text, UniqueConstraint, create_engine, delete, func,
    insert, inspect, select, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from pgvector.sqlalchemy import Vector


BACKEND_DIR = Path(__file__).resolve().parent.parent
metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", String(64), primary_key=True),
    Column("identifier", String(254), nullable=False),
    Column("identifier_normalized", String(254), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("identifier_normalized", name="users_identifier_normalized_key"),
)
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("sessions_expiry_idx", sessions.c.expires_at)
login_attempts = Table(
    "login_attempts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("address_key", String(128), nullable=False),
    Column("attempted_at", Float, nullable=False),
)
Index("login_attempts_window_idx", login_attempts.c.address_key, login_attempts.c.attempted_at)
documents = Table(
    "documents", metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("filename", Text, nullable=False),
    Column("stored_path", Text),
    Column("storage_key", Text),
    Column("pages", Integer, nullable=False),
    Column("chunks", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("owner_id", "filename", name="documents_owner_filename_key"),
    UniqueConstraint("owner_id", "id", name="documents_owner_id_key"),
)
Index("documents_owner_idx", documents.c.owner_id)
document_chunks = Table(
    "document_chunks", metadata,
    Column("id", String(160), primary_key=True),
    Column("owner_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("document_id", String(64), nullable=False),
    Column("source", Text, nullable=False),
    Column("page_number", Integer, nullable=False),
    Column("chunk_index", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("embedding", Vector(384).with_variant(JSON(), "sqlite"), nullable=False),
    ForeignKeyConstraint(
        ["owner_id", "document_id"], ["documents.owner_id", "documents.id"],
        name="document_chunks_owner_document_fkey", ondelete="CASCADE",
    ),
    UniqueConstraint("owner_id", "document_id", "page_number", "chunk_index", name="document_chunks_position_key"),
)
Index("document_chunks_owner_document_idx", document_chunks.c.owner_id, document_chunks.c.document_id)
Index("document_chunks_owner_source_idx", document_chunks.c.owner_id, document_chunks.c.source)
Index("document_chunks_embedding_hnsw_idx", document_chunks.c.embedding, postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})
study_materials = Table(
    "study_materials", metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("title", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("materials_owner_kind_idx", study_materials.c.owner_id, study_materials.c.kind, study_materials.c.created_at)
quizzes = Table(
    "quizzes", metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("questions_json", Text, nullable=False),
    Column("submission_json", Text),
    Column("result_json", Text),
    Column("created_at", Float, nullable=False),
)
Index("quizzes_owner_idx", quizzes.c.owner_id)

_engines: dict[str, Engine] = {}


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required; configure PostgreSQL before starting the application.")
    if value.startswith(("postgres://", "postgresql://")):
        value = "postgresql+psycopg://" + value.split("://", 1)[1]
    if os.getenv("APP_ENV", "").lower() == "production" and not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("Production requires DATABASE_URL to use PostgreSQL; SQLite is supported only for local tests/import tooling.")
    return value


def engine() -> Engine:
    url = database_url()
    if url not in _engines:
        options = {"pool_pre_ping": True}
        if url.startswith("postgresql+"):
            options.update(pool_size=5, max_overflow=10)
        _engines[url] = create_engine(url, **options)
        if os.getenv("APP_ENV", "").lower() == "test" and _engines[url].dialect.name == "sqlite":
            metadata.create_all(_engines[url])
    return _engines[url]


def dispose_engines() -> None:
    for configured_engine in _engines.values():
        configured_engine.dispose()
    _engines.clear()


def data_dir() -> Path:
    configured = os.getenv("APP_DATA_DIR", "").strip()
    return Path(configured).resolve() if configured else (BACKEND_DIR / "data").resolve()


def database_path() -> Path:
    """Legacy SQLite path, retained only for the explicit import utility."""
    return data_dir() / "app.sqlite3"


def initialize_database() -> None:
    """Check connectivity; production schema creation belongs to Alembic."""
    configured_engine = engine()
    with configured_engine.connect():
        pass
    if os.getenv("APP_ENV", "").lower() == "test" and configured_engine.dialect.name == "sqlite":
        metadata.create_all(configured_engine)
        return
    present_tables = set(inspect(configured_engine).get_table_names())
    required_tables = set(metadata.tables) | {"alembic_version"}
    missing_tables = required_tables - present_tables
    if missing_tables:
        raise RuntimeError("Database schema is missing or not migrated; run `alembic upgrade head` before starting the application.")


@contextmanager
def connect():
    """Compatibility context yielding a SQLAlchemy connection transaction."""
    with engine().begin() as connection:
        yield connection


def _dict(row):
    return dict(row) if row is not None else None


def normalize_identifier(identifier: str) -> str:
    return identifier.strip().casefold()


def create_user(identifier: str, password_hash: str) -> dict | None:
    user = {"id": secrets.token_hex(16), "identifier": identifier.strip(), "identifier_normalized": normalize_identifier(identifier), "password_hash": password_hash, "created_at": time.time()}
    try:
        with engine().begin() as connection:
            connection.execute(insert(users).values(**user))
    except IntegrityError:
        return None
    return user


def get_user_by_identifier(identifier: str) -> dict | None:
    query = select(users.c.id, users.c.identifier, users.c.password_hash, users.c.created_at).where(users.c.identifier_normalized == normalize_identifier(identifier))
    with engine().connect() as connection:
        return _dict(connection.execute(query).mappings().first())


def get_user_by_id(user_id: str) -> dict | None:
    query = select(users.c.id, users.c.identifier, users.c.created_at).where(users.c.id == user_id)
    with engine().connect() as connection:
        return _dict(connection.execute(query).mappings().first())


def save_session(token_hash: str, user_id: str, expires_at: float) -> None:
    now = time.time()
    with engine().begin() as connection:
        connection.execute(delete(sessions).where(sessions.c.expires_at <= now))
        connection.execute(insert(sessions).values(token_hash=token_hash, user_id=user_id, expires_at=expires_at, created_at=now))


def get_session_user(token_hash: str) -> dict | None:
    now = time.time()
    query = select(users.c.id, users.c.identifier).select_from(
        sessions.join(users, users.c.id == sessions.c.user_id)
    ).where(sessions.c.token_hash == token_hash, sessions.c.expires_at > now)
    with engine().begin() as connection:
        row = _dict(connection.execute(query).mappings().first())
        connection.execute(delete(sessions).where(sessions.c.expires_at <= now))
    return row


def delete_session(token_hash: str) -> None:
    with engine().begin() as connection:
        connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash))


def count_recent_attempts(address_key: str, since: float) -> int:
    with engine().begin() as connection:
        connection.execute(delete(login_attempts).where(login_attempts.c.attempted_at < since))
        count = connection.execute(select(func.count()).select_from(login_attempts).where(login_attempts.c.address_key == address_key, login_attempts.c.attempted_at >= since)).scalar_one()
    return int(count)


def record_login_attempt(address_key: str, attempted_at: float | None = None) -> None:
    with engine().begin() as connection:
        connection.execute(insert(login_attempts).values(address_key=address_key, attempted_at=attempted_at or time.time()))


def clear_login_attempts(address_key: str) -> None:
    with engine().begin() as connection:
        connection.execute(delete(login_attempts).where(login_attempts.c.address_key == address_key))


def create_document(owner_id: str, filename: str, stored_path: str | None, pages: int, chunks: int, document_id: str, storage_key: str | None = None) -> bool:
    try:
        with engine().begin() as connection:
            connection.execute(insert(documents).values(id=document_id, owner_id=owner_id, filename=filename, stored_path=stored_path, storage_key=storage_key, pages=pages, chunks=chunks, created_at=time.time()))
    except IntegrityError:
        return False
    return True


def list_documents(owner_id: str) -> list[dict]:
    query = select(documents.c.id.label("document_id"), documents.c.filename, documents.c.pages, documents.c.chunks, documents.c.stored_path, documents.c.storage_key, documents.c.created_at).where(documents.c.owner_id == owner_id).order_by(func.lower(documents.c.filename))
    with engine().connect() as connection:
        return [dict(row) for row in connection.execute(query).mappings()]


def get_document(owner_id: str, filename: str) -> dict | None:
    query = select(documents.c.id.label("document_id"), documents.c.owner_id, documents.c.filename, documents.c.pages, documents.c.chunks, documents.c.stored_path, documents.c.storage_key, documents.c.created_at).where(documents.c.owner_id == owner_id, documents.c.filename == filename)
    with engine().connect() as connection:
        return _dict(connection.execute(query).mappings().first())


def delete_document_record(owner_id: str, document_id: str) -> dict | None:
    with engine().begin() as connection:
        query = select(documents).where(documents.c.owner_id == owner_id, documents.c.id == document_id)
        row = _dict(connection.execute(query).mappings().first())
        if row:
            connection.execute(delete(documents).where(documents.c.owner_id == owner_id, documents.c.id == document_id))
    return row


def save_material(owner_id: str, kind: str, title: str, payload: dict, material_id: str | None = None) -> str:
    material_id = material_id or secrets.token_hex(16)
    with engine().begin() as connection:
        connection.execute(insert(study_materials).values(id=material_id, owner_id=owner_id, kind=kind, title=title, payload_json=json.dumps(payload, ensure_ascii=False), created_at=time.time()))
    return material_id


def _decode_material(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "title": row["title"], "data": json.loads(row["payload_json"]), "created_at": row["created_at"]}


def list_materials(owner_id: str, kind: str | None = None) -> list[dict]:
    query = select(study_materials.c.id, study_materials.c.kind, study_materials.c.title, study_materials.c.payload_json, study_materials.c.created_at).where(study_materials.c.owner_id == owner_id)
    if kind:
        query = query.where(study_materials.c.kind == kind)
    query = query.order_by(study_materials.c.created_at.desc()).limit(100)
    with engine().connect() as connection:
        materials = [_decode_material(row) for row in connection.execute(query).mappings()]

    if kind in (None, "quiz"):
        query = select(quizzes.c.id, quizzes.c.questions_json, quizzes.c.created_at).where(quizzes.c.owner_id == owner_id, quizzes.c.submission_json.is_(None)).order_by(quizzes.c.created_at.desc()).limit(100)
        with engine().connect() as connection:
            active_quizzes = list(connection.execute(query).mappings())
        for quiz in active_quizzes:
            questions = json.loads(quiz["questions_json"])
            materials.append({"id": quiz["id"], "kind": "quiz", "title": "Quiz in progress", "created_at": quiz["created_at"], "data": {"quiz_id": quiz["id"], "questions": [{key: item[key] for key in ("id", "question", "options", "sources")} for item in questions]}})
    return sorted(materials, key=lambda material: material["created_at"], reverse=True)[:100]


def get_material(owner_id: str, material_id: str) -> dict | None:
    query = select(study_materials.c.id, study_materials.c.kind, study_materials.c.title, study_materials.c.payload_json, study_materials.c.created_at).where(study_materials.c.owner_id == owner_id, study_materials.c.id == material_id)
    with engine().connect() as connection:
        row = _dict(connection.execute(query).mappings().first())
    if row:
        return _decode_material(row)
    quiz = get_quiz(material_id, owner_id)
    if not quiz or quiz["submission"] is not None:
        return None
    return {"id": quiz["id"], "kind": "quiz", "title": "Quiz in progress", "created_at": quiz["created_at"], "data": {"quiz_id": quiz["id"], "questions": [{key: item[key] for key in ("id", "question", "options", "sources")} for item in quiz["questions"]]}}


def save_quiz(quiz_id: str, owner_id: str, questions: list[dict]) -> None:
    with engine().begin() as connection:
        connection.execute(insert(quizzes).values(id=quiz_id, owner_id=owner_id, questions_json=json.dumps(questions, ensure_ascii=False), created_at=time.time()))


def get_quiz(quiz_id: str, owner_id: str) -> dict | None:
    query = select(quizzes).where(quizzes.c.id == quiz_id, quizzes.c.owner_id == owner_id)
    with engine().connect() as connection:
        row = _dict(connection.execute(query).mappings().first())
    if not row:
        return None
    return {"id": row["id"], "owner_id": row["owner_id"], "questions": json.loads(row["questions_json"]), "submission": json.loads(row["submission_json"]) if row["submission_json"] else None, "result": json.loads(row["result_json"]) if row["result_json"] else None, "created_at": row["created_at"]}


def save_quiz_submission(quiz_id: str, owner_id: str, answers: dict, result: dict) -> bool:
    statement = update(quizzes).where(quizzes.c.id == quiz_id, quizzes.c.owner_id == owner_id, quizzes.c.submission_json.is_(None)).values(submission_json=json.dumps(answers), result_json=json.dumps(result, ensure_ascii=False))
    with engine().begin() as connection:
        result_proxy = connection.execute(statement)
    return result_proxy.rowcount == 1
