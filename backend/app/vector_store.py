
from pathlib import Path
import math
import os
from threading import Lock

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import database


# Resolve paths relative to the backend directory.
BACKEND_DIR = Path(__file__).resolve().parent.parent
CHROMA_PATH = database.data_dir() / "chroma"

# Multilingual model: supports English and Hindi.
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

class _LazyEmbeddingModel:
    """Load the existing embedding model on first search or ingestion use."""

    def __init__(self):
        self._model = None
        self._load_lock = Lock()

    def encode(self, *args, **kwargs):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    # Import PyTorch/Sentence Transformers only when an embedding
                    # is actually requested, and keep one model per process.
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(MODEL_NAME)
        return self._model.encode(*args, **kwargs)


# Keep the existing model and API while allowing auth-only routes and tests to start offline.
embedding_model = _LazyEmbeddingModel()

class _LazyCollection:
    """Open the existing persistent collection only when vector work is requested."""

    def __init__(self):
        self._collection = None
        self._client = None

    def _get(self):
        if self._collection is None:
            # Hosted pgvector workers never import or initialize ChromaDB.
            import chromadb
            self._client = chromadb.PersistentClient(path=str(CHROMA_PATH))
            self._collection = self._client.get_or_create_collection(
                name="notebook_documents",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def __getattr__(self, name):
        return getattr(self._get(), name)


# Do not open, reset, or recreate vector data during authentication-only operations.
collection = _LazyCollection()


def backend() -> str:
    configured = os.getenv("VECTOR_STORE_BACKEND", "chroma").strip().lower()
    if configured not in {"chroma", "pgvector"}:
        raise RuntimeError("VECTOR_STORE_BACKEND must be either 'chroma' or 'pgvector'.")
    if os.getenv("APP_ENV", "").lower() == "production" and configured != "pgvector":
        raise RuntimeError("Production requires VECTOR_STORE_BACKEND=pgvector; Chroma is for local development only.")
    if configured == "pgvector":
        url = database.database_url()
        sqlite_test_backend = os.getenv("APP_ENV", "").lower() == "test" and url.startswith("sqlite:")
        if not url.startswith("postgresql+") and not sqlite_test_backend:
            raise RuntimeError("The pgvector backend requires a PostgreSQL DATABASE_URL.")
    return configured


def _metadata_for_chunk(chunk: dict) -> dict:
    metadata = chunk["metadata"]
    return {
        "owner_id": metadata["owner_id"],
        "document_id": metadata["document_id"],
        "source": metadata["source"],
        "page_number": int(metadata["page_number"]),
        "chunk_index": int(metadata["chunk_index"]),
    }


def _store_pgvector(chunks: list[dict], embeddings: list[list[float]]) -> int:
    table = database.document_chunks
    rows = []
    for chunk, embedding in zip(chunks, embeddings):
        meta = _metadata_for_chunk(chunk)
        if len(embedding) != 384:
            raise ValueError("The configured embedding model must return 384-dimensional vectors.")
        rows.append({
            "id": f"{meta['owner_id']}__{meta['document_id']}__page_{meta['page_number']}__chunk_{meta['chunk_index']}",
            **meta,
            "content": chunk["text"],
            "embedding": embedding,
        })
    target_engine = database.engine()
    dialect_insert = postgres_insert if target_engine.dialect.name == "postgresql" else sqlite_insert
    statement = dialect_insert(table).values(rows)
    updates = {key: getattr(statement.excluded, key) for key in ("source", "page_number", "chunk_index", "content", "embedding")}
    statement = statement.on_conflict_do_update(index_elements=["id"], set_=updates)
    with target_engine.begin() as connection:
        connection.execute(statement)
    return len(rows)


def _distance(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)


def _pgvector_search(owner_id: str, query_embedding: list[float], top_k: int, sources: list[str] | None) -> list[dict]:
    table = database.document_chunks
    distance = table.c.embedding.cosine_distance(query_embedding).label("distance")
    statement = select(table.c.content, table.c.source, table.c.page_number, table.c.chunk_index, distance).where(table.c.owner_id == owner_id)
    if sources is not None:
        if not sources:
            return []
        statement = statement.where(table.c.source.in_(sources))
    statement = statement.order_by(distance).limit(top_k)
    configured_engine = database.engine()
    # Isolated SQLite tests use JSON vectors; hosted execution uses pgvector's
    # indexed cosine operator above.
    if configured_engine.dialect.name == "sqlite":
        filters = [table.c.owner_id == owner_id]
        if sources is not None:
            if not sources:
                return []
            filters.append(table.c.source.in_(sources))
        plain = select(table.c.content, table.c.source, table.c.page_number, table.c.chunk_index, table.c.embedding).where(*filters)
        with configured_engine.connect() as connection:
            rows = list(connection.execute(plain).mappings())
        rows = sorted(rows, key=lambda row: _distance(query_embedding, row["embedding"]))[:top_k]
        get_distance = lambda row: _distance(query_embedding, row["embedding"])
    else:
        with configured_engine.connect() as connection:
            rows = list(connection.execute(statement).mappings())
        get_distance = lambda row: float(row["distance"])

    return [{
        "text": row["content"],
        "metadata": {"source": row["source"], "page_number": row["page_number"], "chunk_index": row["chunk_index"]},
        "distance": get_distance(row),
    } for row in rows]


def store_chunks(chunks: list[dict]) -> int:
    """Convert chunks to embeddings and store them in ChromaDB."""
    if not chunks:
        return 0

    texts = [chunk["text"] for chunk in chunks]
    embeddings = embedding_model.encode(
        texts,
        normalize_embeddings=True,
    ).tolist()

    if backend() == "pgvector":
        return _store_pgvector(chunks, embeddings)

    metadatas = [chunk["metadata"] for chunk in chunks]

    # New chunks use opaque owner/document IDs so equal filenames never collide.
    ids = []
    for metadata in metadatas:
        if metadata.get("owner_id") and metadata.get("document_id"):
            identifier = f"{metadata['owner_id']}__{metadata['document_id']}"
        else:
            identifier = str(metadata["source"])
        ids.append(f"{identifier}__page_{metadata['page_number']}__chunk_{metadata['chunk_index']}")

    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    return len(chunks)


def search_chunks(
    owner_id: str,
    question: str,
    top_k: int = 4,
    sources: list[str] | None = None,
) -> list[dict]:
    """Find the chunks most semantically similar to a question."""
    question_embedding = embedding_model.encode(
        [question],
        normalize_embeddings=True,
    ).tolist()

    if backend() == "pgvector":
        return _pgvector_search(owner_id, question_embedding[0], top_k, sources)

    matches = []
    query_options = {"where": {"owner_id": owner_id}}
    if sources is not None:
        if not sources:
            return matches
        query_options["where"] = {"$and": [{"owner_id": owner_id}, {"source": {"$in": sources}}]}

    count = collection.count()
    if count == 0:
        return matches

    results = collection.query(
        query_embeddings=question_embedding,
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
        **query_options,
    )

    if not results["documents"] or not results["documents"][0]:
        return matches

    for document, metadata, distance in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        matches.append({
            "text": document,
            "metadata": metadata,
            "distance": distance,
        })

    return matches


def get_document_chunks(sources: list[str], owner_id: str) -> list[dict]:
    """Return every stored text chunk for the exact requested documents."""
    if not sources:
        return []

    if backend() == "pgvector":
        table = database.document_chunks
        statement = select(table.c.id, table.c.document_id, table.c.content, table.c.source, table.c.page_number, table.c.chunk_index).where(
            table.c.owner_id == owner_id, table.c.source.in_(sources),
        ).order_by(table.c.source, table.c.page_number, table.c.chunk_index)
        with database.engine().connect() as connection:
            rows = list(connection.execute(statement).mappings())
        return [{
            "id": row["id"], "text": row["content"],
            "metadata": {"owner_id": owner_id, "document_id": row["document_id"], "source": row["source"], "page_number": row["page_number"], "chunk_index": row["chunk_index"]},
        } for row in rows]

    result = collection.get(
        where={"$and": [{"owner_id": owner_id}, {"source": {"$in": sources}}]},
        include=["documents", "metadatas"],
    )
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    ids = result.get("ids") or []

    return [
        {"text": text, "metadata": metadata, "id": identifier}
        for text, metadata, identifier in zip(documents, metadatas, ids)
        if text and metadata
    ]


def list_documents(owner_id: str) -> list[dict]:
    """List only documents belonging to this authenticated owner."""
    return [
        {
            **{key: document[key] for key in ("document_id", "filename", "pages", "chunks")},
            "download_available": bool(document.get("storage_key") or (document.get("stored_path") and Path(document["stored_path"]).is_file())),
        }
        for document in database.list_documents(owner_id)
    ]


def delete_document(owner_id: str, filename: str) -> int:
    """Delete the authenticated owner's document, vectors, and persisted PDF."""
    document = database.get_document(owner_id, filename)
    if not document:
        return 0

    # Delete a hosted object only after the owner-scoped metadata lookup above.
    if document.get("storage_key"):
        from app.supabase_storage import delete_pdf
        delete_pdf(document["storage_key"])

    ids = delete_document_vectors(owner_id, document["document_id"])
    database.delete_document_record(owner_id, document["document_id"])
    stored_path = document.get("stored_path")
    if stored_path:
        try:
            Path(stored_path).unlink(missing_ok=True)
        except OSError:
            pass
    return ids


def delete_document_vectors(owner_id: str, document_id: str) -> int:
    """Remove vectors for exactly one owner/document pair."""
    if backend() == "pgvector":
        with database.engine().begin() as connection:
            result = connection.execute(delete(database.document_chunks).where(
                database.document_chunks.c.owner_id == owner_id,
                database.document_chunks.c.document_id == document_id,
            ))
        return result.rowcount or 0

    result = collection.get(where={"$and": [{"owner_id": owner_id}, {"document_id": document_id}]}, include=["metadatas"])
    ids = result["ids"]
    if ids:
        collection.delete(ids=ids)
    return len(ids)
