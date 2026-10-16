"""Auth, persistence, and ownership tests using isolated SQLAlchemy SQLite and Chroma stores."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_DATA = tempfile.TemporaryDirectory(prefix="mini-notebooklm-auth-tests-")
os.environ["APP_DATA_DIR"] = _TEST_DATA.name

from fastapi.testclient import TestClient
import numpy as np
import chromadb
import uuid

from app import database
from app.auth import hash_password, verify_password
from app.main import app
from app import vector_store
from app.database import metadata
from sqlalchemy import create_engine, insert, inspect


class MemoryCollection:
    """Tiny Chroma-compatible test double; tests never open or write the real index."""

    def __init__(self):
        self.entries = {}

    @staticmethod
    def _matches(metadata, where):
        if "$and" in where:
            return all(MemoryCollection._matches(metadata, clause) for clause in where["$and"])
        for key, value in where.items():
            if isinstance(value, dict) and "$in" in value:
                if metadata.get(key) not in value["$in"]:
                    return False
            elif metadata.get(key) != value:
                return False
        return True

    def upsert(self, ids, documents, embeddings, metadatas):
        for identifier, document, metadata in zip(ids, documents, metadatas):
            self.entries[identifier] = {"document": document, "metadata": dict(metadata)}

    def get(self, where=None, include=None):
        matched = [(identifier, entry) for identifier, entry in self.entries.items() if where is None or self._matches(entry["metadata"], where)]
        return {"ids": [item[0] for item in matched], "documents": [item[1]["document"] for item in matched], "metadatas": [item[1]["metadata"] for item in matched]}

    def count(self):
        return len(self.entries)

    def query(self, query_embeddings, n_results, include, where=None):
        matched = [(identifier, entry) for identifier, entry in self.entries.items() if where is None or self._matches(entry["metadata"], where)][:n_results]
        return {"ids": [[item[0] for item in matched]], "documents": [[item[1]["document"] for item in matched]], "metadatas": [[item[1]["metadata"] for item in matched]], "distances": [[0.0 for _ in matched]]}

    def delete(self, ids):
        for identifier in ids:
            self.entries.pop(identifier, None)

    def update(self, ids, metadatas):
        for identifier, metadata in zip(ids, metadatas):
            self.entries[identifier]["metadata"] = dict(metadata)


vector_store.collection = MemoryCollection()


TEST_PASSWORD = "A-long-test-password-42!"
ORIGIN = "http://localhost:5173"


class AuthenticationAndIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password(TEST_PASSWORD)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mini-notebooklm-db-test-")
        self.env = patch.dict(os.environ, {
            "APP_DATA_DIR": self.temp.name,
            "DATABASE_URL": f"sqlite:///{(Path(self.temp.name) / 'test.sqlite3').as_posix()}",
            "AUTH_SECRET": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY",
            "AUTH_ALLOWED_ORIGINS": ORIGIN,
            "APP_ENV": "test",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(database.dispose_engines)
        self.client = TestClient(app)

    def csrf(self, client=None):
        client = client or self.client
        response = client.get("/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def register(self, identifier, password=TEST_PASSWORD, client=None):
        client = client or self.client
        token = self.csrf(client)
        return client.post(
            "/auth/register",
            json={"identifier": identifier, "password": password},
            headers={"Origin": ORIGIN, "X-CSRF-Token": token},
        )

    def login(self, identifier, password=TEST_PASSWORD, client=None):
        client = client or self.client
        token = self.csrf(client)
        response = client.post(
            "/auth/login",
            json={"identifier": identifier, "password": password},
            headers={"Origin": ORIGIN, "X-CSRF-Token": token},
        )
        return response, token

    def test_registration_validation_duplicate_detection_and_password_hashing(self):
        response = self.register("Learner_One")
        self.assertEqual(response.status_code, 201)
        duplicate = self.register("learner_one")
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.register("bad username", "short")
        self.assertEqual(invalid.status_code, 422)
        six_character_password = self.register("six_char_user", "abc123")
        self.assertEqual(six_character_password.status_code, 201)
        five_character_password = self.register("five_char_user", "abc12")
        self.assertEqual(five_character_password.status_code, 422)
        stored = database.get_user_by_identifier("LEARNER_ONE")
        self.assertNotEqual(stored["password_hash"], TEST_PASSWORD)
        self.assertTrue(verify_password(TEST_PASSWORD, stored["password_hash"]))
        self.assertIsNone(database.get_user_by_identifier("bad username"))

    def test_successful_and_failed_login_and_session_restoration(self):
        self.assertEqual(self.register("learner@example.test").status_code, 201)
        token_value = self.client.cookies.get("mini_notebooklm_session")
        csrf_value = self.client.cookies.get("mini_notebooklm_csrf")
        self.assertTrue(token_value)
        cookie = next(cookie for cookie in self.client.cookies.jar if cookie.name == "mini_notebooklm_session")
        self.assertTrue(cookie.has_nonstandard_attr("HttpOnly"))
        self.assertEqual(self.client.get("/auth/me").json(), {"authenticated": True, "identifier": "learner@example.test"})

        returning = TestClient(app)
        returning.cookies.set("mini_notebooklm_session", token_value)
        returning.cookies.set("mini_notebooklm_csrf", csrf_value)
        self.assertEqual(returning.get("/auth/me").status_code, 200)
        failed_client = TestClient(app)
        failure, _ = self.login("learner@example.test", "wrong-password", failed_client)
        self.assertEqual(failure.status_code, 401)
        self.assertIsNone(failed_client.cookies.get("mini_notebooklm_session"))

    def test_logout_revokes_persistent_session(self):
        self.register("logout_user")
        token = self.client.cookies.get("mini_notebooklm_csrf")
        response = self.client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_csrf_origin_and_production_cookie_controls(self):
        token = self.csrf()
        missing = self.client.post("/auth/register", json={"identifier": "csrf_user", "password": TEST_PASSWORD}, headers={"Origin": ORIGIN})
        wrong_origin = self.client.post("/auth/register", json={"identifier": "csrf_user", "password": TEST_PASSWORD}, headers={"Origin": "https://attacker.example", "X-CSRF-Token": token})
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong_origin.status_code, 403)
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            secure_response = self.client.get("/auth/csrf")
        cookie = secure_response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=strict", cookie)
        with patch.dict(os.environ, {"APP_ENV": "production", "AUTH_COOKIE_SAMESITE": "none"}):
            cross_site_response = self.client.get("/auth/csrf")
        cross_site_cookie = cross_site_response.headers["set-cookie"].lower()
        self.assertIn("secure", cross_site_cookie)
        self.assertIn("samesite=none", cross_site_cookie)
        with patch.dict(os.environ, {"APP_ENV": "development", "AUTH_COOKIE_SAMESITE": "none"}):
            invalid_cookie_mode = self.client.get("/auth/csrf")
        self.assertEqual(invalid_cookie_mode.status_code, 503)

    def test_cors_allows_configured_frontend_with_credentials(self):
        response = self.client.options("/documents", headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-csrf-token",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")

    def test_supabase_storage_client_uses_private_authenticated_routes(self):
        from app.supabase_storage import delete_pdf, download_pdf, upload_pdf

        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-only-storage-key",
            "SUPABASE_STORAGE_BUCKET": "documents",
            "APP_ENV": "production",
        }), patch("app.supabase_storage.httpx.Client") as client_type:
            client = client_type.return_value.__enter__.return_value
            response = client.request.return_value
            response.content = b"%PDF-test"
            response.raise_for_status.return_value = None
            key = upload_pdf("owner123", "document456", b"%PDF-test")
            downloaded = download_pdf(key)
            delete_pdf(key)

        self.assertEqual(key, "owner123/document456.pdf")
        self.assertEqual(downloaded, b"%PDF-test")
        requests = client.request.call_args_list
        self.assertEqual([call.args[0] for call in requests], ["POST", "GET", "DELETE"])
        self.assertIn("/object/documents/owner123/document456.pdf", requests[0].args[1])
        self.assertIn("/object/authenticated/documents/owner123/document456.pdf", requests[1].args[1])
        self.assertEqual(requests[2].kwargs["json"], {"prefixes": [key]})
        self.assertEqual(requests[0].kwargs["headers"]["Authorization"], "Bearer test-only-storage-key")

    def test_pgvector_backend_search_and_delete_are_owner_scoped(self):
        from app.vector_store import delete_document_vectors, get_document_chunks, search_chunks, store_chunks

        owner_a = database.create_user("vector_user_a", self.password_hash)
        owner_b = database.create_user("vector_user_b", self.password_hash)
        doc_a, doc_b = "vector-doc-a", "vector-doc-b"
        self.assertTrue(database.create_document(owner_a["id"], "shared.pdf", None, 1, 1, doc_a))
        self.assertTrue(database.create_document(owner_b["id"], "shared.pdf", None, 1, 1, doc_b))
        chunks = [
            {"text": "A private passage", "metadata": {"owner_id": owner_a["id"], "document_id": doc_a, "source": "shared.pdf", "page_number": 1, "chunk_index": 0}},
            {"text": "B private passage", "metadata": {"owner_id": owner_b["id"], "document_id": doc_b, "source": "shared.pdf", "page_number": 1, "chunk_index": 0}},
        ]

        def encode(texts, **_kwargs):
            values = np.zeros((len(texts), 384), dtype=float)
            for row, value in zip(values, texts):
                row[0 if value.startswith("A") or value == "query" else 1] = 1.0
            return values

        with patch.dict(os.environ, {
            "VECTOR_STORE_BACKEND": "pgvector",
            "SUPABASE_URL": "https://project.example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-only-storage-key",
        }), patch(
            "app.vector_store.embedding_model.encode", side_effect=encode,
        ):
            self.assertEqual(store_chunks(chunks), 2)
            result_a = search_chunks(owner_a["id"], "query", top_k=5, sources=["shared.pdf"])
            result_b = search_chunks(owner_b["id"], "query", top_k=5, sources=["shared.pdf"])
            read_a = get_document_chunks(["shared.pdf"], owner_a["id"])
            deleted = delete_document_vectors(owner_a["id"], doc_a)
            remaining_b = search_chunks(owner_b["id"], "query", top_k=5)

        self.assertEqual([item["text"] for item in result_a], ["A private passage"])
        self.assertEqual([item["text"] for item in result_b], ["B private passage"])
        self.assertEqual(read_a[0]["metadata"]["owner_id"], owner_a["id"])
        self.assertEqual(deleted, 1)
        self.assertEqual([item["text"] for item in remaining_b], ["B private passage"])

    def test_hosted_pdf_flow_uses_supabase_without_local_files(self):
        user_a = TestClient(app)
        user_b = TestClient(app)
        self.assertEqual(self.register("hosted_user_a", client=user_a).status_code, 201)
        self.assertEqual(self.register("hosted_user_b", client=user_b).status_code, 201)
        owner_a = database.get_user_by_identifier("hosted_user_a")
        body = b"%PDF-test-content"

        def encode(texts, **_kwargs):
            vectors = np.zeros((len(texts), 384), dtype=float)
            vectors[:, 0] = 1.0
            return vectors

        with patch.dict(os.environ, {
            "VECTOR_STORE_BACKEND": "pgvector",
            "SUPABASE_URL": "https://project.example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-only-storage-key",
        }), patch(
            "app.main.extract_pdf_pages_from_bytes", return_value=[{"page_number": 1, "text": "Hosted PDF content."}],
        ), patch("app.supabase_storage.upload_pdf", side_effect=lambda owner_id, document_id, _content: f"{owner_id}/{document_id}.pdf") as upload, patch(
            "app.supabase_storage.download_pdf", return_value=body,
        ) as download, patch("app.supabase_storage.delete_pdf") as remove, patch(
            "app.vector_store.embedding_model.encode", side_effect=encode,
        ):
            uploaded = user_a.post("/upload-pdf", files={"file": ("hosted.pdf", body, "application/pdf")}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            record = database.get_document(owner_a["id"], "hosted.pdf")
            self.assertIsNone(record["stored_path"])
            self.assertEqual(record["storage_key"], f"{owner_a['id']}/{uploaded.json()['document_id']}.pdf")
            self.assertTrue(user_a.get("/documents").json()["documents"][0]["download_available"])
            self.assertEqual(user_a.get("/documents/hosted.pdf/download").content, body)
            self.assertEqual(user_b.get("/documents/hosted.pdf/download").status_code, 404)
            removed = user_a.delete("/documents/hosted.pdf", headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})

        self.assertEqual(removed.status_code, 200)
        upload.assert_called_once()
        download.assert_called_once_with(record["storage_key"])
        remove.assert_called_once_with(record["storage_key"])
        self.assertIsNone(database.get_document(owner_a["id"], "hosted.pdf"))

    def test_login_rate_limit_is_shared_through_database(self):
        self.register("rate_user")
        client = TestClient(app)
        token = self.csrf(client)
        headers = {"Origin": ORIGIN, "X-CSRF-Token": token}
        for _ in range(10):
            response = client.post("/auth/login", json={"identifier": "rate_user", "password": "incorrect-password"}, headers=headers)
            self.assertEqual(response.status_code, 401)
        blocked = client.post("/auth/login", json={"identifier": "rate_user", "password": "incorrect-password"}, headers=headers)
        self.assertEqual(blocked.status_code, 429)

    def test_all_private_endpoints_reject_anonymous_requests(self):
        cases = [
            ("GET", "/auth/me", None),
            ("GET", "/documents", None),
            ("GET", "/documents/secret.pdf/download", None),
            ("GET", "/materials", None),
            ("GET", "/materials/guessed-id", None),
            ("POST", "/upload-pdf", {"files": {"file": ("sample.pdf", b"%PDF-test", "application/pdf")}}),
            ("POST", "/search", {"json": {"question": "test"}}),
            ("POST", "/ask", {"json": {"question": "test"}}),
            ("POST", "/notes/generate", {"json": {"sources": ["sample.pdf"]}}),
            ("POST", "/flashcards/generate", {"json": {"sources": ["sample.pdf"]}}),
            ("POST", "/quiz/generate", {"json": {"sources": ["sample.pdf"]}}),
            ("POST", "/quiz/submit", {"json": {"quiz_id": "invalid", "answers": {}}}),
            ("DELETE", "/documents/sample.pdf", None),
        ]
        for method, path, kwargs in cases:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, **(kwargs or {}))
                self.assertEqual(response.status_code, 401)

    def _upload_pdf(self, client, filename):
        with patch("app.main.extract_pdf_pages", return_value=[{"page_number": 1, "text": "Private text about a unique process."}]), patch(
            "app.vector_store.embedding_model.encode", side_effect=lambda texts, **_: np.tile(np.array([[1.0, 0.0, 0.0]], dtype=float), (len(texts), 1)),
        ):
            response = client.post(
                "/upload-pdf", files={"file": (filename, b"%PDF-test-content", "application/pdf")},
                headers={"Origin": ORIGIN, "X-CSRF-Token": client.cookies.get("mini_notebooklm_csrf")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_two_user_document_search_chat_delete_and_saved_material_isolation(self):
        user_a = TestClient(app)
        user_b = TestClient(app)
        self.assertEqual(self.register("user_a", client=user_a).status_code, 201)
        self.assertEqual(self.register("user_b", client=user_b).status_code, 201)
        uploaded = self._upload_pdf(user_a, "shared-name.pdf")
        owner_a = database.get_user_by_identifier("user_a")
        owner_b = database.get_user_by_identifier("user_b")
        stored_document = database.get_document(owner_a["id"], "shared-name.pdf")
        self.assertTrue(Path(stored_document["stored_path"]).is_file())
        self.assertEqual(user_a.get("/documents").json()["total_documents"], 1)
        self.assertEqual(user_b.get("/documents").json()["total_documents"], 0)
        vector_store.collection.upsert(
            ids=["previous-owner__page_1__chunk_0"], documents=["Legacy private text"], embeddings=[[]],
            metadatas=[{"source": "legacy-private.pdf", "page_number": 1, "chunk_index": 0}],
        )

        forbidden_search = user_b.post("/search", json={"question": "private", "sources": ["shared-name.pdf"]}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_b.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(forbidden_search.status_code, 404)
        with patch("app.vector_store.embedding_model.encode", side_effect=lambda texts, **_: np.tile(np.array([[1.0, 0.0, 0.0]], dtype=float), (len(texts), 1))):
            empty_search = user_b.post("/search", json={"question": "private"}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_b.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(empty_search.status_code, 200)
        self.assertEqual(empty_search.json()["results_count"], 0)
        guessed_download = user_b.get("/documents/shared-name.pdf/download")
        self.assertEqual(guessed_download.status_code, 404)
        wrong_delete = user_b.delete("/documents/shared-name.pdf", headers={"Origin": ORIGIN, "X-CSRF-Token": user_b.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(wrong_delete.status_code, 404)
        self.assertEqual(user_a.get("/documents").json()["total_documents"], 1)

        with patch("app.main.load_source_batches", return_value=[{"test": "source"}]), patch(
            "app.main.generate_notes", return_value={"title": "Private notes", "overview": "A", "sections": [], "quick_revision": []},
        ):
            notes = user_a.post("/notes/generate", json={"sources": ["shared-name.pdf"]}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(notes.status_code, 200)
        material_id = notes.json()["id"]
        self.assertEqual(user_a.get("/materials?kind=notes").json()["materials"][0]["title"], "Private notes")
        self.assertEqual(user_b.get(f"/materials/{material_id}").status_code, 404)

        with patch("app.main.generate_answer", return_value="Private [shared-name.pdf, p. 1]"), patch(
            "app.vector_store.embedding_model.encode", side_effect=lambda texts, **_: np.tile(np.array([[1.0, 0.0, 0.0]], dtype=float), (len(texts), 1)),
        ):
            answer = user_a.post("/ask", json={"question": "private", "sources": ["shared-name.pdf"]}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["sources"], [{"filename": "shared-name.pdf", "page_number": 1}])
        foreign_chat = user_b.post("/ask", json={"question": "private", "sources": ["shared-name.pdf"]}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_b.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(foreign_chat.status_code, 404)

        with patch("app.main.load_source_batches", return_value=[{"test": "source"}]), patch(
            "app.main.generate_flashcards", return_value=[{"id": "card-1", "question": "Term?", "answer": "Definition", "sources": []}],
        ):
            cards = user_a.post("/flashcards/generate", json={"sources": ["shared-name.pdf"], "card_count": 4}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(cards.status_code, 200)
        self.assertEqual(user_b.get("/materials?kind=flashcards").json()["materials"], [])

        quiz_questions = [{"id": "q1", "question": "Q?", "options": [{"id": code, "text": code} for code in "ABCD"], "correct_option_id": "A", "explanation": "Because.", "sources": [{"filename": "shared-name.pdf", "page_number": 1}]}]
        with patch("app.main.load_source_batches", return_value=[{"test": "source"}]), patch("app.main.generate_quiz_questions", return_value=quiz_questions):
            quiz_response = user_a.post("/quiz/generate", json={"sources": ["shared-name.pdf"], "question_count": 4}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(quiz_response.status_code, 200)
        quiz_id = quiz_response.json()["quiz_id"]
        self.assertNotIn("correct_option_id", quiz_response.text)
        self.assertEqual(user_b.post("/quiz/submit", json={"quiz_id": quiz_id, "answers": {"q1": "A"}}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_b.cookies.get("mini_notebooklm_csrf")}).status_code, 404)
        submitted = user_a.post("/quiz/submit", json={"quiz_id": quiz_id, "answers": {"q1": "A"}}, headers={"Origin": ORIGIN, "X-CSRF-Token": user_a.cookies.get("mini_notebooklm_csrf")})
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["score"], 1)
        self.assertEqual(user_a.get("/materials?kind=quiz").json()["materials"][0]["data"]["score"], 1)
        self.assertEqual(user_b.get(f"/materials/{quiz_id}").status_code, 404)

        resumed = TestClient(app)
        resumed.cookies.set("mini_notebooklm_session", user_a.cookies.get("mini_notebooklm_session"))
        self.assertEqual(resumed.get("/auth/me").status_code, 200)
        self.assertEqual(resumed.get("/documents").json()["total_documents"], 1)
        self.assertTrue(uploaded["document_id"])

    def test_legacy_migration_claims_only_an_explicit_imported_account(self):
        from migrate_legacy_chroma import migrate_legacy_data

        legacy_collection = MemoryCollection()
        legacy_collection.upsert(
            ids=["old-file__page_1__chunk_0"], documents=["Old private indexed content"], embeddings=[[]],
            metadatas=[{"source": "old-file.pdf", "page_number": 1, "chunk_index": 0}],
        )
        owner_id, migrated_count = migrate_legacy_data("former-owner", self.password_hash, legacy_collection)
        self.assertEqual(migrated_count, 1)
        migrated_doc = database.list_documents(owner_id)
        self.assertEqual(migrated_doc[0]["filename"], "old-file.pdf")
        self.assertIsNone(migrated_doc[0]["stored_path"])
        migrated_metadata = legacy_collection.get()["metadatas"][0]
        self.assertEqual(migrated_metadata["owner_id"], owner_id)
        self.assertTrue(migrated_metadata["document_id"])

    def test_sqlite_import_is_repeatable_and_leaves_source_intact(self):
        from import_sqlite_data import import_sqlite_database

        source_path = Path(self.temp.name) / "legacy source.sqlite3"
        source_engine = create_engine(f"sqlite:///{source_path.as_posix()}")
        metadata.create_all(source_engine)
        source_user = {
            "id": "legacy-user-id", "identifier": "legacy-user", "identifier_normalized": "legacy-user",
            "password_hash": self.password_hash, "created_at": 100.0,
        }
        with source_engine.begin() as connection:
            connection.execute(insert(database.users).values(**source_user))
            connection.execute(insert(database.documents).values(
                id="legacy-document-id", owner_id=source_user["id"], filename="legacy.pdf",
                stored_path="C:/old-data/uploads/legacy.pdf", pages=2, chunks=3, created_at=101.0,
            ))

        first = import_sqlite_database(source_path, database.engine())
        second = import_sqlite_database(source_path, database.engine())
        self.assertEqual(first["users"], 1)
        self.assertEqual(first["documents"], 1)
        self.assertTrue(all(count == 0 for count in second.values()))
        self.assertEqual(database.get_user_by_identifier("LEGACY-USER")["id"], "legacy-user-id")
        self.assertEqual(database.get_document("legacy-user-id", "legacy.pdf")["stored_path"], "C:/old-data/uploads/legacy.pdf")
        self.assertTrue(source_path.is_file())
        source_engine.dispose()

    def test_alembic_initial_revision_runs_on_isolated_sqlite(self):
        from alembic import command
        from alembic.config import Config

        target_path = Path(self.temp.name) / "alembic target.sqlite3"
        target_url = f"sqlite:///{target_path.as_posix()}"
        with patch.dict(os.environ, {"DATABASE_URL": target_url, "APP_ENV": "test"}):
            config = Config(str(Path(__file__).parent / "alembic.ini"))
            command.upgrade(config, "head")
        migration_engine = create_engine(target_url)
        names = set(inspect(migration_engine).get_table_names())
        self.assertTrue(set(("users", "sessions", "login_attempts", "documents", "study_materials", "quizzes", "document_chunks", "alembic_version")).issubset(names))
        self.assertIn("storage_key", {column["name"] for column in inspect(migration_engine).get_columns("documents")})
        migration_engine.dispose()

    def test_database_configuration_requires_explicit_postgres_in_production(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///must-not-fallback.sqlite3", "APP_ENV": "production"}):
            with self.assertRaisesRegex(RuntimeError, "requires DATABASE_URL.*PostgreSQL"):
                database.database_url()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://tester:placeholder@localhost/example", "APP_ENV": "production"}):
            self.assertEqual(database.database_url(), "postgresql+psycopg://tester:placeholder@localhost/example")

    def test_real_chroma_metadata_filter_is_tenant_scoped(self):
        client = chromadb.EphemeralClient()
        index = client.create_collection(name=f"tenant_{uuid.uuid4().hex[:12]}")
        index.add(
            ids=["a-private", "b-private"],
            documents=["User A chunk", "User B chunk"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            metadatas=[{"owner_id": "user-a", "document_id": "doc-a", "source": "same.pdf"}, {"owner_id": "user-b", "document_id": "doc-b", "source": "same.pdf"}],
        )
        response = index.query(
            query_embeddings=[[1.0, 0.0]], n_results=2,
            where={"$and": [{"owner_id": "user-a"}, {"source": {"$in": ["same.pdf"]}}]},
        )
        self.assertEqual(response["documents"], [["User A chunk"]])


if __name__ == "__main__":
    unittest.main()
