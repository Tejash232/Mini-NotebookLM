Mini NotebookLM

An AI-powered document assistant. Upload PDFs, ask questions in natural language, and get answers grounded in your own documents, with page-level citations. It can also generate study notes, flashcards and quizzes from the same material.

Built to go beyond "call an LLM API": it covers the full RAG pipeline from ingestion to retrieval to generation, with authentication and per-user data isolation.

<!-- Add a screenshot or short GIF here, e.g. ![Demo](docs/demo.gif) -->

Live demo: <!-- https://your-frontend-url -->

The backend runs on a free instance, so the first request after a period of inactivity can take a minute while it wakes up.

Features
PDF library: upload, list, download and delete documents (up to 30 MB each)
Semantic search: multilingual vector embeddings over document chunks
Q&A with citations: Retrieval-Augmented Generation answers using only the selected sources, citing [filename, p. N]
Study tools: AI-generated notes, flashcards and graded multiple-choice quizzes, saved to your account
Accounts and isolation: register/login, with every document, chunk and material scoped to its owner
Cloud-ready storage: private Supabase Storage for PDFs and pgvector for embeddings in production
How it works
PDF ──► text extraction (PyMuPDF) ──► chunking (1000 chars, 150 overlap)
    ──► embeddings (multilingual MiniLM, 384-dim) ──► vector store

Question ──► embed ──► top-k similar chunks (owner + source filtered)
         ──► prompt with context ──► Groq LLM ──► answer + citations
Layer	Local development	Hosted
Vector search	ChromaDB	PostgreSQL + pgvector (HNSW, cosine)
PDF storage	Local filesystem	Supabase Storage (private bucket)
Database	PostgreSQL	PostgreSQL (Supabase)
Tech stack
Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic
Frontend: React, Vite
Embeddings: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
LLM: Groq (default llama-3.3-70b-versatile, configurable with GROQ_MODEL)
Database and vectors: PostgreSQL, pgvector, ChromaDB (local)
Storage: Supabase Storage
Project structure
.
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI routes
│   │   ├── auth.py             # password hashing, sessions, CSRF
│   │   ├── ingest.py           # PDF text extraction
│   │   ├── chunking.py         # text splitting
│   │   ├── vector_store.py     # embeddings + Chroma / pgvector
│   │   ├── generation.py       # grounded Q&A via Groq
│   │   ├── study_materials.py  # notes, flashcards, quizzes
│   │   ├── supabase_storage.py # private PDF storage
│   │   └── database.py         # SQLAlchemy models and queries
│   ├── migrations/             # Alembic schema migrations
│   └── requirements.txt
└── frontend/                   # React + Vite app
Getting started (local)
Prerequisites
Python 3.12
Node.js 18+
PostgreSQL (a local database and role)
A Groq API key
1. Backend
bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env

Edit backend/.env and set at least:

env
APP_ENV=development
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/mini_notebooklm
AUTH_SECRET=<32+ random bytes, URL-safe base64>
GROQ_API_KEY=<your key>
VECTOR_STORE_BACKEND=chroma

Generate a secret with:

bash
python -c "import secrets; print(secrets.token_urlsafe(32))"

Apply migrations and start the API:

bash
alembic upgrade head
python -m uvicorn app.main:app --reload

The API runs at http://localhost:8000. The first upload downloads the embedding model, so expect a delay once.

2. Frontend
bash
cd frontend
npm install
npm run dev

Open http://localhost:5173. The Vite dev server proxies /api to the backend, so leave VITE_API_BASE_URL unset locally.

Deployment (Render + Supabase)

The full hosted setup, including environment variables, the CPU-only PyTorch build command, bucket configuration and backups, is documented in backend/README.md. In short:

Create a Supabase project, enable the vector extension, and create a private Storage bucket named documents.
Deploy the backend to Render with APP_ENV=production, VECTOR_STORE_BACKEND=pgvector and the Supabase and Groq secrets.
Run alembic upgrade head against the production database as a release step.
Deploy the frontend with VITE_API_BASE_URL set to the API's origin, and add that origin to AUTH_ALLOWED_ORIGINS.
API overview
Method	Endpoint	Purpose
POST	/auth/register, /auth/login, /auth/logout	Account and session handling
POST	/upload-pdf	Upload and index a PDF
GET	/documents	List your documents
GET / DELETE	/documents/{filename}	Download (/download) or delete
POST	/search	Semantic search across selected sources
POST	/ask	Grounded Q&A with citations
POST	/notes/generate, /flashcards/generate, /quiz/generate	Study materials
POST	/quiz/submit	Grade a quiz
GET	/materials	Saved study materials
GET	/health	Health check

Interactive docs are available at /docs when the API is running.

Security
PBKDF2 password hashing and opaque, hashed, server-side sessions in HttpOnly cookies
CSRF tokens on state-changing requests and an exact-origin allowlist
Login throttling
All queries scoped to the authenticated owner, including vector retrieval and deletion
Private storage bucket; the Supabase service-role key stays server-side only
Tests
bash
cd backend
python -m unittest test_auth -v

The Supabase and pgvector tests mock external calls and use SQLite, so they do not prove PostgreSQL runtime compatibility. Run a disposable PostgreSQL integration test before relying on a deployment.

Known limitations
Memory: loading PyTorch and the embedding model can exceed a 512 MB instance during uploads. A larger instance or a separate embedding service is the lasting fix.
Cold starts: on free hosting, the first upload after inactivity may be slow while the service and model load.
Study tools on large PDFs: notes, flashcards and quizzes process the whole document in batches and can hit LLM rate limits on very large files.
Single-turn Q&A: each question is answered independently, with no conversation memory.
Roadmap
 Background processing for uploads, with a "processing" status
 Similarity threshold so unrelated questions return "not found"
 Conversation history for follow-up questions
 Lighter embedding setup for small instances
 CI with automated tests
