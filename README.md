# Mini NotebookLM

An AI-powered document assistant. Upload PDFs, ask questions in natural language, and get answers grounded in your own documents, with page-level citations. It can also generate study notes, flashcards and quizzes from your material.

## Features

- Upload and manage PDF documents
- Semantic search using vector embeddings
- Context-aware question answering with Retrieval-Augmented Generation (RAG)
- AI-generated study notes, flashcards and quizzes
- User accounts with per-user document isolation
- Cloud-based document storage

## How it works

```
PDF ──► text extraction ──► chunking ──► embeddings ──► vector store

Question ──► embed ──► find similar chunks ──► LLM ──► answer with citations
```

1. **Ingest:** PDFs are parsed page by page and split into overlapping chunks.
2. **Embed:** each chunk is converted to a vector with a multilingual sentence-transformers model.
3. **Retrieve:** a question is embedded and matched against your document chunks.
4. **Generate:** the best matches are passed to the LLM, which answers using only that context and cites the source and page.

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Alembic
- **Frontend:** React, Vite
- **Database:** PostgreSQL
- **Vector search:** ChromaDB (local), pgvector (hosted)
- **Storage:** Supabase Storage
- **Embeddings:** sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`)
- **LLM:** Groq

## Project structure

```
.
├── backend/     # FastAPI app, database models, migrations
│   └── app/     # routes, auth, ingestion, chunking, vector store, generation
└── frontend/    # React + Vite app
```

## Getting started

You will need Python 3.12, Node.js, PostgreSQL and a Groq API key.

**Backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # then fill in the values
alembic upgrade head
python -m uvicorn app.main:app --reload
```

Set these in `backend/.env`:

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string |
| `AUTH_SECRET` | Random secret, at least 32 bytes |
| `GROQ_API_KEY` | Your Groq API key |

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

For hosted deployment (Render and Supabase), see `backend/README.md`.
