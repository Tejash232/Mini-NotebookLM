
import os
import re
import hmac
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4
from pathlib import Path
from fastapi import Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.generation import generate_answer
from app import database
from app.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    allowed_origins,
    clear_login_failures,
    create_csrf_token,
    is_production,
    issue_session,
    login_rate_limited,
    record_failed_login,
    require_allowed_origin,
    require_csrf,
    require_session,
    revoke_session,
    valid_csrf_token,
    verify_password,
    hash_password,
    validate_identifier,
    validate_password,
)

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.ingest import extract_pdf_pages
from app.chunking import split_pages_into_chunks
from app.vector_store import (
    store_chunks,
    search_chunks,
    list_documents,
    delete_document,
    delete_document_vectors,
)
from app.study_materials import (
    generate_flashcards,
    generate_notes,
    generate_quiz_questions,
    load_source_batches,
    public_quiz,
)

@asynccontextmanager
async def lifespan(_app):
    # Verify the explicitly configured database is reachable on startup.
    # Production schema changes must already have been applied by Alembic.
    database.initialize_database()
    yield


app = FastAPI(title="Mini NotebookLM", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins()),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

_DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-password")


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(LoginRequest):
    pass


def _set_cookie(response: Response, name: str, value: str, *, http_only: bool, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=http_only,
        secure=is_production(),
        samesite="strict",
        path="/",
    )


@app.get("/auth/csrf")
def csrf_bootstrap(response: Response):
    token = create_csrf_token()
    _set_cookie(response, CSRF_COOKIE, token, http_only=True, max_age=SESSION_TTL_SECONDS)
    return {"csrf_token": token}


@app.get("/auth/me")
def auth_status(user: dict = Depends(require_session)):
    return {"authenticated": True, "identifier": user["identifier"]}


@app.post("/auth/login")
def login(request: Request, response: Response, credentials: LoginRequest):
    require_allowed_origin(request)
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    csrf_header = request.headers.get("x-csrf-token", "")
    if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header) or not valid_csrf_token(csrf_cookie):
        raise HTTPException(status_code=403, detail="A valid CSRF token is required.")

    client_key = request.client.host if request.client else "unknown"
    if login_rate_limited(client_key):
        raise HTTPException(status_code=429, detail="Too many login attempts. Wait one minute and try again.")
    user = database.get_user_by_identifier(credentials.identifier)
    password_ok = verify_password(credentials.password, user["password_hash"] if user else _DUMMY_PASSWORD_HASH)
    if not user or not password_ok:
        record_failed_login(client_key)
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    clear_login_failures(client_key)
    session_token = issue_session(user["id"])
    _set_cookie(response, SESSION_COOKIE, session_token, http_only=True, max_age=SESSION_TTL_SECONDS)
    return {"authenticated": True, "identifier": user["identifier"]}


@app.post("/auth/register", status_code=201)
def register(request: Request, response: Response, credentials: RegisterRequest):
    require_allowed_origin(request)
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    csrf_header = request.headers.get("x-csrf-token", "")
    if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header) or not valid_csrf_token(csrf_cookie):
        raise HTTPException(status_code=403, detail="A valid CSRF token is required.")

    address_key = request.client.host if request.client else "unknown"
    if login_rate_limited(address_key):
        raise HTTPException(status_code=429, detail="Too many account attempts. Wait one minute and try again.")
    identifier = validate_identifier(credentials.identifier)
    validate_password(credentials.password)
    user = database.create_user(identifier, hash_password(credentials.password))
    if not user:
        record_failed_login(address_key)
        raise HTTPException(status_code=409, detail="An account with that email or username already exists.")
    clear_login_failures(address_key)
    session_token = issue_session(user["id"])
    _set_cookie(response, SESSION_COOKIE, session_token, http_only=True, max_age=SESSION_TTL_SECONDS)
    return {"authenticated": True, "identifier": user["identifier"]}


@app.post("/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(require_csrf)):
    del user
    revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", secure=is_production(), httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=is_production(), httponly=True, samesite="strict")
    return {"authenticated": False}
class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=4, ge=1, le=10)
    sources: list[str] | None = None


class StudySourcesRequest(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=20)


class NotesRequest(StudySourcesRequest):
    style: Literal["study_guide", "summary", "detailed"] = "study_guide"
    detail_level: Literal["concise", "balanced", "thorough"] = "balanced"


class FlashcardsRequest(StudySourcesRequest):
    card_count: int = Field(default=12, ge=4, le=40)
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class QuizRequest(StudySourcesRequest):
    question_count: int = Field(default=10, ge=4, le=30)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    question_type: Literal["multiple_choice"] = "multiple_choice"


class QuizSubmission(BaseModel):
    quiz_id: str
    answers: dict[str, str]


MAX_UPLOAD_BYTES = 30 * 1024 * 1024


@app.get("/")
def home():
    return {"message": "Mini NotebookLM API is running!"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...), user: dict = Depends(require_csrf)):
    raw_filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not raw_filename or not raw_filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a PDF file.",
        )

    upload_directory = database.data_dir() / "uploads" / user["id"]
    upload_directory.mkdir(parents=True, exist_ok=True)
    document_id = uuid4().hex
    persistent_path = upload_directory / f"{document_id}.pdf"

    total_bytes = 0
    try:
        with persistent_path.open("wb") as target:
            while block := await file.read(1024 * 1024):
                total_bytes += len(block)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="PDF files must be 30 MB or smaller.")
                target.write(block)
    except Exception:
        persistent_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    with persistent_path.open("rb") as source_file:
        is_pdf = source_file.read(5) == b"%PDF-"
    if not is_pdf:
        persistent_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is not a valid PDF.",
        )

    if database.get_document(user["id"], raw_filename):
        persistent_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="A document with this filename already exists in your workspace.")

    try:
        pages = extract_pdf_pages(str(persistent_path))

        if not pages:
            raise HTTPException(
                status_code=422,
                detail="No extractable text found. The PDF may be scanned.",
            )

        chunks = split_pages_into_chunks(
            pages=pages,
            filename=raw_filename,
        )
        for chunk in chunks:
            chunk["metadata"]["owner_id"] = user["id"]
            chunk["metadata"]["document_id"] = document_id

        stored_count = store_chunks(chunks)
        if not database.create_document(user["id"], raw_filename, str(persistent_path), len(pages), stored_count, document_id):
            delete_document_vectors(user["id"], document_id)
            persistent_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="A document with this filename already exists in your workspace.")

        return {
            "document_id": document_id,
            "filename": raw_filename,
            "total_pages_extracted": len(pages),
            "total_chunks": len(chunks),
            "chunks_stored": stored_count,
            "message": "PDF processed and stored successfully.",
        }

    except Exception:
        # If indexing/metadata persistence failed, remove partial vectors and the PDF.
        delete_document_vectors(user["id"], document_id)
        persistent_path.unlink(missing_ok=True)
        raise


@app.post("/search")
def search_documents(request: SearchRequest, user: dict = Depends(require_csrf)):
    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty.",
        )

    try:
        sources = validate_owned_sources(user["id"], request.sources)
        results = search_chunks(
            owner_id=user["id"],
            question=question,
            top_k=request.top_k,
            sources=sources,
        )
        results = [
            {
                **result,
                "metadata": {
                    key: result.get("metadata", {}).get(key)
                    for key in ("source", "page_number", "chunk_index")
                    if key in result.get("metadata", {})
                },
            }
            for result in results
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(exc)}",
        ) from exc

    return {
        "question": question,
        "results_count": len(results),
        "results": results,
    }

@app.post("/ask")
def ask_question(request: SearchRequest, user: dict = Depends(require_csrf)):
    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty.",
        )

    try:
        # Stage 1: Retrieve relevant passages.
        sources = validate_owned_sources(user["id"], request.sources)
        chunks = search_chunks(
            owner_id=user["id"],
            question=question,
            top_k=request.top_k,
            sources=sources,
        )

        # Stage 2: Generate an answer from those passages.
        answer = generate_answer(
            question=question,
            chunks=chunks,
        )

        # Report only retrieved filename/page pairs that the answer cites.
        retrieved_sources = set()
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            filename = metadata.get("source")
            page_number = metadata.get("page_number")
            if filename and page_number is not None:
                retrieved_sources.add((str(filename), str(page_number)))

        cited_sources = []
        seen = set()
        citation_pattern = re.compile(r"\[([^\]\n]+?),\s*p\.\s*(\d+)\]", re.IGNORECASE)
        for match in citation_pattern.finditer(answer):
            cited_filename = match.group(1).strip()
            cited_page = match.group(2)
            key = next(
                (
                    source_key
                    for source_key in retrieved_sources
                    if source_key[0].casefold() == cited_filename.casefold()
                    and source_key[1] == cited_page
                ),
                None,
            )
            if key is not None and key not in seen:
                seen.add(key)
                cited_sources.append({"filename": key[0], "page_number": int(key[1])})

        return {
            "question": question,
            "answer": answer,
            "sources": cited_sources,
            "retrieved_chunks": len(chunks),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Question answering failed: {str(exc)}",
        ) from exc


def validate_owned_sources(owner_id: str, sources: list[str] | None) -> list[str] | None:
    if sources is None:
        return None
    allowed = {document["filename"] for document in list_documents(owner_id)}
    missing = [source for source in sources if source not in allowed]
    if missing:
        raise HTTPException(status_code=404, detail="One or more selected documents are unavailable in your workspace.")
    return sources


@app.post("/notes/generate")
def create_notes(request: NotesRequest, user: dict = Depends(require_csrf)):
    batches = load_source_batches(request.sources, user["id"])
    notes = generate_notes(batches, request.style, request.detail_level)
    material_id = database.save_material(user["id"], "notes", notes["title"], notes)
    notes["id"] = material_id
    return notes


@app.post("/flashcards/generate")
def create_flashcards(request: FlashcardsRequest, user: dict = Depends(require_csrf)):
    batches = load_source_batches(request.sources, user["id"])
    cards = generate_flashcards(batches, request.card_count, request.difficulty)
    material = {"cards": cards, "total_cards": len(cards)}
    material_id = database.save_material(user["id"], "flashcards", f"{len(cards)} flashcards", material)
    material["id"] = material_id
    return material


@app.post("/quiz/generate")
def create_quiz(request: QuizRequest, user: dict = Depends(require_csrf)):
    batches = load_source_batches(request.sources, user["id"])
    questions = generate_quiz_questions(batches, request.question_count, request.difficulty)
    quiz_id = uuid4().hex
    database.save_quiz(quiz_id, user["id"], questions)
    return public_quiz(quiz_id, questions)


@app.post("/quiz/submit")
def submit_quiz(request: QuizSubmission, user: dict = Depends(require_csrf)):
    quiz = database.get_quiz(request.quiz_id, user["id"])
    if quiz is None:
        raise HTTPException(status_code=404, detail="This quiz does not exist in your account.")
    if quiz["submission"] is not None:
        if quiz["submission"] == request.answers:
            return quiz["result"]
        raise HTTPException(status_code=409, detail="This quiz has already been submitted.")

    questions = quiz["questions"]
    question_ids = {question["id"] for question in questions}
    if set(request.answers) != question_ids:
        raise HTTPException(status_code=422, detail="Submit exactly one selected option for every quiz question.")

    results = []
    score = 0
    for question in questions:
        selected_option_id = request.answers[question["id"]]
        valid_option_ids = {option["id"] for option in question["options"]}
        if selected_option_id not in valid_option_ids:
            raise HTTPException(status_code=422, detail=f"Invalid option for question {question['id']}.")
        correct = selected_option_id == question["correct_option_id"]
        score += int(correct)
        results.append({
            "id": question["id"],
            "question": question["question"],
            "options": question["options"],
            "selected_option_id": selected_option_id,
            "correct_option_id": question["correct_option_id"],
            "is_correct": correct,
            "explanation": question["explanation"],
            "sources": question["sources"],
        })

    result = {
        "quiz_id": request.quiz_id,
        "score": score,
        "total_questions": len(questions),
        "percentage": round((score / len(questions)) * 100),
        "questions": results,
    }
    if not database.save_quiz_submission(request.quiz_id, user["id"], request.answers, result):
        updated = database.get_quiz(request.quiz_id, user["id"])
        if updated and updated["submission"] == request.answers:
            return updated["result"]
        raise HTTPException(status_code=409, detail="This quiz has already been submitted.")
    database.save_material(user["id"], "quiz", f"Quiz result: {score}/{len(questions)}", result, material_id=request.quiz_id)
    return result


@app.get("/documents")
def get_documents(user: dict = Depends(require_session)):
    """List all uploaded documents."""
    documents = list_documents(user["id"])

    return {
        "total_documents": len(documents),
        "documents": documents,
    }


@app.delete("/documents/{filename}")
def remove_document(filename: str, user: dict = Depends(require_csrf)):
    """Delete a document and its stored chunks."""
    deleted_chunks = delete_document(user["id"], filename)

    if deleted_chunks == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{filename}' was not found.",
        )

    return {
        "message": f"Document '{filename}' deleted successfully.",
        "filename": filename,
        "deleted_chunks": deleted_chunks,
    }


@app.get("/documents/{filename}/download")
def download_document(filename: str, user: dict = Depends(require_session)):
    document = database.get_document(user["id"], filename)
    if not document or not document.get("stored_path") or not Path(document["stored_path"]).is_file():
        raise HTTPException(status_code=404, detail="The original PDF is not available in your workspace.")
    return FileResponse(document["stored_path"], filename=document["filename"], media_type="application/pdf")


@app.get("/materials")
def get_materials(kind: Literal["notes", "flashcards", "quiz"] | None = None, user: dict = Depends(require_session)):
    return {"materials": database.list_materials(user["id"], kind)}


@app.get("/materials/{material_id}")
def get_material(material_id: str, user: dict = Depends(require_session)):
    material = database.get_material(user["id"], material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Saved material was not found in your account.")
    return material
