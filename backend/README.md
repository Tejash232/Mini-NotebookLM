# Mini NotebookLM backend

FastAPI stores users, password hashes, persistent sessions, login throttles, owned document metadata, saved study materials, and quiz keys in PostgreSQL through SQLAlchemy. Alembic owns the schema. The app does not create production tables automatically. Uploaded PDFs and Chroma remain filesystem data under `APP_DATA_DIR`; the default Chroma location remains `backend/data/chroma`.

## Local setup

1. Install PostgreSQL and create an empty database and role for this app. For example, in `psql`:

   ```sql
   CREATE ROLE mini_notebooklm LOGIN PASSWORD 'choose-a-local-password';
   CREATE DATABASE mini_notebooklm OWNER mini_notebooklm;
   ```

   Keep the password private. For deployment, use a managed PostgreSQL service or an equivalent secured PostgreSQL installation.

2. From `backend`, create/activate a virtual environment and install dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` only if `.env` does not already exist. Set `DATABASE_URL` to the PostgreSQL connection string, `AUTH_SECRET` to a random URL-safe secret of at least 32 bytes, and configure the existing `GROQ_API_KEY`. `APP_DATA_DIR` may remain empty to retain the current `backend/data` paths. Never commit `.env`.

   Example URL shape: `postgresql+psycopg://mini_notebooklm:<url-encoded-password>@localhost:5432/mini_notebooklm`.

4. Apply schema migrations from `backend`:

   ```powershell
   .venv\Scripts\alembic.exe upgrade head
   ```

5. Start FastAPI from `backend` with `.venv\Scripts\python.exe -m uvicorn app.main:app --reload`, and start Vite from `frontend` with `npm.cmd run dev`.

`DATABASE_URL` is mandatory. SQLite is accepted only when explicitly configured for isolated local tests or the SQLite import utility. With `APP_ENV=production`, startup rejects a SQLite URL rather than falling back to a local file. Authentication keeps PBKDF2-SHA256 password hashes, opaque hashed sessions, HttpOnly cookies, CSRF tokens, exact Origin checks, login throttling, and owner-scoped access.

## PostgreSQL schema and changes

Alembic revision `0001_initial_schema` creates:

- `users`, `sessions`, and `login_attempts`
- `documents` with a foreign-key owner and unique per-owner filename
- `study_materials` and `quizzes` with owner foreign keys

IDs and JSON payloads remain portable strings/text, and timestamps remain epoch seconds, matching the existing application interface. Add future schema changes as new Alembic revisions. Review migrations before applying them; production startup never calls `create_all`.

Useful commands from `backend`:

```powershell
.venv\Scripts\alembic.exe current
.venv\Scripts\alembic.exe history
.venv\Scripts\alembic.exe upgrade head
```

## Existing SQLite data import

The SQLite database and filesystem data are not modified or imported automatically. `import_sqlite_data.py` reads the SQLite source in read-only mode and imports users, password hashes, sessions, throttling records, document rows, study materials, and quiz answer keys in one PostgreSQL transaction. Re-running it is safe: identical existing primary-key rows are skipped; conflicting rows stop the import rather than overwrite data. It does not touch Chroma or copy/delete PDFs.

Before a real migration:

1. Stop FastAPI and any worker processes. Back up the entire current `APP_DATA_DIR` (or `backend/data` if unset), including `app.sqlite3`, any `app.sqlite3-wal`/`app.sqlite3-shm`, `uploads/`, and `chroma/`. Keep a second protected copy of the backup.
2. Keep the old application data and `.env` untouched. Provision a new PostgreSQL database and configure the new deployment's `DATABASE_URL` to it.
3. Run `alembic upgrade head` against the intended PostgreSQL database and verify the Alembic revision before importing.
4. Run the importer explicitly, pointing it at the backed-up SQLite file:

   ```powershell
   .venv\Scripts\python.exe import_sqlite_data.py --source C:\secure-backup\app.sqlite3
   ```

   The importer refuses a non-PostgreSQL target in CLI mode and prints inserted row counts only. Run it first against a disposable restored copy if practical.
5. Restore/copy the original `uploads/` and `chroma/` directories without replacing or resetting the source copies. Preserve existing absolute PDF paths recorded in SQLite, or keep `APP_DATA_DIR` at the same absolute location. If you relocate uploaded files, update the `stored_path` values in PostgreSQL to the corresponding copied paths before serving traffic.
6. Verify account login, document list/download, chat/search, study materials, and Chroma results. Keep the SQLite backup and old files until the new deployment is confirmed and backed up.

The application continues to filter all Chroma reads/deletes by `owner_id` and document identity. Existing unowned vectors remain hidden until the existing explicit legacy Chroma ownership migration is run. Do not run that separate migration as part of the SQL import without a Chroma backup and a deliberate ownership decision.

## Backup and restore

Back up PostgreSQL and filesystem data together so database paths still resolve:

```powershell
pg_dump --format=custom --file=C:\secure-backup\mini-notebooklm.dump "$env:DATABASE_URL"
```

Also back up the full `APP_DATA_DIR`, especially `uploads/` and `chroma/`, using your platform's protected backup tooling. Protect `.env` and backups as secrets; PostgreSQL contains password hashes and user data.

For restore, provision a fresh empty PostgreSQL database and restore into it:

```powershell
pg_restore --dbname="$env:DATABASE_URL" C:\secure-backup\mini-notebooklm.dump
```

Restore the matching `APP_DATA_DIR` backup to its expected path, then verify the database and files before starting the app. Do not restore over a live database or delete the existing SQLite/Chroma/PDF copy as a cleanup step.

## Deployment

- Use PostgreSQL with TLS, restricted network access, automated backups, and a least-privilege application role.
- Set `DATABASE_URL`, `AUTH_SECRET`, `AUTH_ALLOWED_ORIGINS`, `APP_ENV=production`, `GROQ_API_KEY`, and a durable `APP_DATA_DIR` through the deployment secret manager.
- Apply `alembic upgrade head` as a reviewed deployment step before starting web workers. Do not enable SQLite fallback or schema auto-creation.
- Keep `APP_DATA_DIR/uploads` and `APP_DATA_DIR/chroma` on durable storage with access controls. Back up these files with PostgreSQL.
- Serve the frontend and API over HTTPS on the same site (or proxy `/api` to FastAPI as configured in Vite) so Secure, SameSite cookies work. Set CORS/Origin allowlists to exact trusted origins.
- PostgreSQL supports concurrent API workers. Use durable shared file storage or an appropriate shared/object storage design before running workers across hosts; the current PDF and Chroma paths are local filesystem based.

## Tests

Run from `backend`:

```powershell
.venv\Scripts\python.exe -m unittest test_auth -v
.venv\Scripts\python.exe -m unittest discover -v
```

The auth/integration suite uses per-test temporary SQLite databases through SQLAlchemy and an in-memory Chroma-compatible test double. The Chroma filter test uses an ephemeral Chroma collection. These tests validate database API compatibility and isolation behavior but are **not tests against PostgreSQL**. Set up a dedicated disposable PostgreSQL database and add/run a separate PostgreSQL integration suite before claiming PostgreSQL runtime verification.
