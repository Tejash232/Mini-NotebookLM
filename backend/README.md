# Mini NotebookLM backend

FastAPI stores accounts, sessions, document metadata, study materials, quizzes, and (when configured) vector chunks in PostgreSQL through SQLAlchemy. Alembic owns the schema. Local development defaults to the existing persistent Chroma collection and PDF files under `APP_DATA_DIR`; hosted production requires pgvector and private Supabase Storage so uploaded PDFs and application data do not depend on Render's ephemeral filesystem.

## Local development

1. Install PostgreSQL and create a local database/role, then from `backend` create a virtual environment and install dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` only if `.env` does not already exist. Set `DATABASE_URL`, `AUTH_SECRET` (at least 32 random bytes, URL-safe base64), `GROQ_API_KEY`, and `APP_ENV=development`. Leave `VECTOR_STORE_BACKEND=chroma` and `APP_DATA_DIR` empty to retain the existing local paths. Keep `.env` private.

   Example local database URL shape: `postgresql+psycopg://mini_notebooklm:<url-encoded-password>@localhost:5432/mini_notebooklm`.

3. Apply reviewed schema migrations and start the services:

   ```powershell
   .venv\Scripts\alembic.exe upgrade head
   .venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

   In another terminal, from `frontend`, run `npm.cmd install` and `npm.cmd run dev`. The Vite `/api` proxy remains enabled for local development; leave `VITE_API_BASE_URL` unset locally.

SQLite is only supported by isolated tests and the legacy import utility. Production startup rejects SQLite and rejects Chroma. Authentication retains password hashing, opaque persisted sessions, HttpOnly cookies, CSRF tokens, exact Origin allowlists, login throttling, and owner-scoped API access.

## Render + Supabase hosted configuration

The repository root pins Render to Python `3.12.11` in `.python-version` (Render also allows an exact `PYTHON_VERSION` service variable, which takes precedence). This avoids silently using Render's newer default Python. For the Render service, use a CPU-only PyTorch wheel before installing the remaining requirements so the later Sentence Transformers dependency resolves to the already-installed CPU build. If Render's **Root Directory** is `backend`, set this **Build Command**:

```bash
python -m pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu && python -m pip install --no-cache-dir -r requirements.txt
```

If the service root is the repository root instead, prefix both commands with `cd backend &&`. Keep the pinned CPU install first; do not use a CUDA wheel index. Local Chroma development still installs and uses the same model and embedding dimensions; this only controls the Render environment's PyTorch build.

The model and Chroma imports are lazy. In hosted `pgvector` mode, startup imports neither ChromaDB nor Sentence Transformers/PyTorch. The process creates a single cached Sentence Transformer model the first time an embedding is needed. This lowers startup memory use, but does not make embedding work fit within every 512 MB instance: loading the existing model, PyTorch runtime, PDF parsing, and request memory can still exceed that limit during upload, chat, notes, flashcards, or quiz generation. The embedding model is intentionally unchanged to keep vectors compatible. If embedding requests still exhaust memory, a larger memory tier or a separate embedding worker/service is needed; do not change models without rebuilding compatible vectors.

Create a Supabase project, enable the `vector` extension (the migration also uses `CREATE EXTENSION IF NOT EXISTS vector`), and create or verify that the private Storage bucket named `documents` exists. Keep the bucket private. Do not put the service role key in frontend variables. Configure the Render backend's secret environment variables:

| Variable | Hosted value |
| --- | --- |
| `APP_ENV` | `production` |
| `DATABASE_URL` | Supabase PostgreSQL connection URL using the `psycopg` SQLAlchemy driver format (`postgresql+psycopg://...`) |
| `VECTOR_STORE_BACKEND` | `pgvector` |
| `SUPABASE_URL` | Supabase project URL (`https://<project-ref>.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side service role key, stored only in Render secrets |
| `SUPABASE_STORAGE_BUCKET` | `documents` |
| `AUTH_SECRET` | A new random URL-safe base64 secret of at least 32 bytes |
| `AUTH_ALLOWED_ORIGINS` | Exact HTTPS origin(s) of the deployed frontend, comma-separated, no paths |
| `AUTH_COOKIE_SAMESITE` | `strict` when frontend and API are same-site; use `none` only for cross-site deployment, over HTTPS |
| `GROQ_API_KEY` | Server-side Groq key in Render secrets |

Set the frontend build variable `VITE_API_BASE_URL` to the deployed FastAPI origin (for example `https://your-api-service.onrender.com`). The frontend sends credentialed requests and its existing CSRF token; backend CORS and Origin checks must list the exact deployed frontend origin. Same-site frontend/API hosting or a same-site reverse proxy is the most reliable cookie setup. Cross-site cookies require `AUTH_COOKIE_SAMESITE=none` and HTTPS (`Secure`); browsers may still restrict third-party cookies.

Use Supabase's PostgreSQL URL and network options appropriate for the Render service. Run migrations as a reviewed release/setup step before starting the API, from `backend`:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATABASE_URL = "<set privately in your shell or deployment secret manager>"
.venv\Scripts\alembic.exe upgrade head
```

Do not put credentials directly in shell history or commit them. In Render, use its environment/secret configuration and a release command or one-time deployment step for Alembic; do not run migrations against a live database until it has been backed up and reviewed.

## Schema and vector storage

Alembic revision `0001_initial_schema` creates users, sessions, login throttles, documents, study materials, and quizzes. Revision `0002_supabase_pgvector_storage` is additive: it adds nullable `documents.storage_key`, a composite owner/document constraint, `document_chunks` with 384-dimensional embeddings, owner/document/source indexes, and an HNSW cosine index. It enables the PostgreSQL `vector` extension if needed. It does not drop existing tables or recreate Chroma data. Future changes should use new Alembic revisions; production startup does not call `create_all`.

Local `VECTOR_STORE_BACKEND=chroma` is the default and preserves the existing `backend/data/chroma` collection. Hosted `pgvector` stores each chunk with its owner and document IDs, embeds with the existing multilingual 384-dimensional model, and filters retrieval and deletion by owner. Hosted PDF upload parses the file in memory and writes it to the private Storage bucket under `<owner-id>/<document-id>.pdf`; PostgreSQL stores the storage key and metadata. Authenticated download and owner-scoped deletion use that key. No essential uploaded PDF or vector is written to Render's local disk in hosted mode. Render's free filesystem is ephemeral, so it must not be used for app data or uploads ([Render filesystem guidance](https://render.com/docs/faq)). Model/cache files may be downloaded to ephemeral runtime storage and can require a cold-start download.

## Existing local data and migration limits

No local PDF, SQLite database, or Chroma data is migrated, deleted, or modified by switching configuration. SQLite account/document import (if needed) does not copy PDFs or Chroma chunks. Existing local Chroma vectors and filesystem PDFs therefore remain local and are not automatically available in a hosted pgvector/Supabase deployment. Moving existing user content requires a separate, deliberate migration of document metadata, PDF objects, and embeddings with verified ownership and backups; this deployment setup does not run that migration.

Before any real data migration, stop the app and back up the full existing data directory (`app.sqlite3` including WAL/SHM files, `uploads/`, and `chroma/`) to protected storage, plus a second backup. Provision a separate target database, test the migration on a restored copy, verify account/document ownership and content, and retain the original copies until a restore has been proven. Do not run the legacy SQLite importer or any content migration against production without a reviewed plan and backup.

## Useful Alembic commands

From `backend`:

```powershell
.venv\Scripts\alembic.exe current
.venv\Scripts\alembic.exe history
.venv\Scripts\alembic.exe upgrade head
```

## Backup and restore

Back up PostgreSQL and Supabase Storage together. Use `pg_dump` for PostgreSQL and Supabase's supported backup/export process for Storage objects; verify both backups and access controls. Do not rely on the Render filesystem for a backup. Example database backup (keep the URL private):

```powershell
pg_dump --format=custom --file=C:\secure-backup\mini-notebooklm.dump "$env:DATABASE_URL"
```

Restore into a fresh, isolated PostgreSQL database after provisioning required extensions, then restore Storage objects and verify database storage keys match. Example:

```powershell
pg_restore --dbname="$env:DATABASE_URL" C:\secure-backup\mini-notebooklm.dump
```

Never restore over a live database or delete source backups as a cleanup step.

## Tests

From `backend`:

```powershell
.venv\Scripts\python.exe -m unittest test_auth test_chunking -v
.venv\Scripts\python.exe -m unittest discover -v
```

The hosted storage and pgvector tests mock Supabase HTTP calls and use isolated SQLite with JSON vectors; the Chroma test uses an ephemeral collection. These tests do **not** establish PostgreSQL or Supabase runtime compatibility. A dedicated disposable PostgreSQL integration run and a Supabase staging test remain necessary before production. Never point tests at production data.
