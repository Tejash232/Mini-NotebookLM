"""Explicitly attach old unowned Chroma chunks to the former owner account.

Run only after backing up backend/data and setting the old owner credentials as
LEGACY_OWNER_IDENTIFIER and LEGACY_OWNER_PASSWORD_HASH. Existing vectors are
updated in place; no embeddings or collection reset are performed.
"""

import os
import uuid
from collections import defaultdict
import hmac
from dotenv import load_dotenv

from app import database
from app.auth import _parse_password_hash
from app.vector_store import collection


def migrate_legacy_data(identifier: str, password_hash: str, vector_collection=collection) -> tuple[str, int]:
    _parse_password_hash(password_hash)
    identifier = identifier.strip()
    if not identifier:
        raise ValueError("LEGACY_OWNER_IDENTIFIER is required.")
    owner = database.get_user_by_identifier(identifier)
    if owner:
        if not hmac.compare_digest(owner["password_hash"], password_hash):
            raise ValueError("That account already exists with different credentials. Refusing to assign legacy documents to it.")
    else:
        owner = database.create_user(identifier, password_hash)
        if not owner:
            raise ValueError("Could not create the legacy owner's account.")

    result = vector_collection.get(include=["metadatas"])
    groups = defaultdict(list)
    for vector_id, metadata in zip(result.get("ids") or [], result.get("metadatas") or []):
        if metadata and metadata.get("source") and metadata.get("owner_id") in (None, owner["id"]):
            groups[str(metadata["source"])].append((vector_id, dict(metadata)))

    migrated = 0
    for filename, entries in groups.items():
        existing_ids = {metadata.get("document_id") for _, metadata in entries if metadata.get("document_id")}
        if len(existing_ids) > 1:
            raise ValueError(f"Legacy metadata for {filename!r} has inconsistent document IDs; no vectors for that file were changed.")
        document_id = next(iter(existing_ids), uuid.uuid4().hex)
        updated = []
        for vector_id, metadata in entries:
            metadata["owner_id"] = owner["id"]
            metadata["document_id"] = document_id
            updated.append((vector_id, metadata))
        for offset in range(0, len(updated), 250):
            batch = updated[offset:offset + 250]
            vector_collection.update(ids=[item[0] for item in batch], metadatas=[item[1] for item in batch])

        pages = {int(metadata["page_number"]) for _, metadata in entries if metadata.get("page_number") is not None}
        current = database.get_document(owner["id"], filename)
        if current:
            if current["document_id"] != document_id:
                raise ValueError(f"A document named {filename!r} already exists for the imported account.")
        else:
            if not database.create_document(owner["id"], filename, None, len(pages), len(entries), document_id):
                raise ValueError(f"Could not record legacy document {filename!r}.")
        migrated += len(entries)
    return owner["id"], migrated


if __name__ == "__main__":
    load_dotenv()
    legacy_identifier = os.getenv("LEGACY_OWNER_IDENTIFIER") or os.getenv("OWNER_USERNAME", "")
    legacy_hash = os.getenv("LEGACY_OWNER_PASSWORD_HASH") or os.getenv("OWNER_PASSWORD_HASH", "")
    if not legacy_identifier or not legacy_hash:
        raise SystemExit("Set LEGACY_OWNER_IDENTIFIER and LEGACY_OWNER_PASSWORD_HASH from the existing owner configuration.")
    try:
        _, vector_count = migrate_legacy_data(legacy_identifier, legacy_hash)
    except Exception as error:
        raise SystemExit(f"Migration stopped safely: {error}") from error
    print(f"Migrated {vector_count} legacy chunks. Existing source PDFs were not present in the old storage and cannot be restored.")
