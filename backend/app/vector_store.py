
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

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

    def encode(self, *args, **kwargs):
        if self._model is None:
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


def store_chunks(chunks: list[dict]) -> int:
    """Convert chunks to embeddings and store them in ChromaDB."""
    if not chunks:
        return 0

    texts = [chunk["text"] for chunk in chunks]
    embeddings = embedding_model.encode(
        texts,
        normalize_embeddings=True,
    ).tolist()

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
            "download_available": bool(document.get("stored_path") and Path(document["stored_path"]).is_file()),
        }
        for document in database.list_documents(owner_id)
    ]


def delete_document(owner_id: str, filename: str) -> int:
    """Delete the authenticated owner's document, vectors, and persisted PDF."""
    document = database.get_document(owner_id, filename)
    if not document:
        return 0
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
    result = collection.get(where={"$and": [{"owner_id": owner_id}, {"document_id": document_id}]}, include=["metadatas"])
    ids = result["ids"]
    if ids:
        collection.delete(ids=ids)
    return len(ids)
