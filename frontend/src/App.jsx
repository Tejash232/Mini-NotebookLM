import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

const API = import.meta.env.VITE_API_BASE_URL || "/api";
const tabs = ["Sources", "Chat", "Notes", "Flashcards", "Quiz"];

export default function App() {
  const [activeTab, setActiveTab] = useState("Sources");
  const [documents, setDocuments] = useState([]);
  const [selectedSources, setSelectedSources] = useState(null);
  const [file, setFile] = useState(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [busyDocument, setBusyDocument] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [working, setWorking] = useState(false);
  const [notesStyle, setNotesStyle] = useState("study_guide");
  const [notesDetail, setNotesDetail] = useState("balanced");
  const [notesResult, setNotesResult] = useState(null);
  const [cardCount, setCardCount] = useState(12);
  const [studyDifficulty, setStudyDifficulty] = useState("medium");
  const [flashcards, setFlashcards] = useState([]);
  const [cardIndex, setCardIndex] = useState(0);
  const [cardFlipped, setCardFlipped] = useState(false);
  const [quizCount, setQuizCount] = useState(10);
  const [quiz, setQuiz] = useState(null);
  const [quizIndex, setQuizIndex] = useState(0);
  const [quizAnswers, setQuizAnswers] = useState({});
  const [quizResult, setQuizResult] = useState(null);
  const [savedMaterials, setSavedMaterials] = useState([]);
  const [authReady, setAuthReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [csrfToken, setCsrfToken] = useState("");
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginPasswordVisible, setLoginPasswordVisible] = useState(false);
  const [confirmPassword, setConfirmPassword] = useState("");
  const [confirmPasswordVisible, setConfirmPasswordVisible] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [loginError, setLoginError] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const fileInput = useRef(null);
  const authBootstrapRef = useRef(null);

  useEffect(() => {
    async function initializeAuth() {
      try {
        const csrfResponse = await fetch(`${API}/auth/csrf`, { credentials: "include" });
        const csrfData = await csrfResponse.json();
        if (!csrfResponse.ok) throw new Error(csrfData.detail || "Authentication service is unavailable.");
        setCsrfToken(csrfData.csrf_token);
        const sessionResponse = await fetch(`${API}/auth/me`, { credentials: "include" });
        setAuthenticated(sessionResponse.ok);
        if (sessionResponse.ok) {
          await loadDocuments();
          await loadMaterials();
        }
      } catch (err) {
        setLoginError(err.message || "Cannot connect to the authentication service.");
      } finally {
        setAuthReady(true);
      }
    }
    if (!authBootstrapRef.current) authBootstrapRef.current = initializeAuth();
  }, []);

  async function refreshCsrfToken() {
    const response = await fetch(`${API}/auth/csrf`, { credentials: "include" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not refresh the secure request token.");
    setCsrfToken(data.csrf_token);
    return data.csrf_token;
  }

  async function apiFetch(path, options = {}, csrfOverride = csrfToken) {
    const method = (options.method || "GET").toUpperCase();
    const headers = new Headers(options.headers || {});
    const send = (csrfValue) => {
      const requestHeaders = new Headers(headers);
      if (!["GET", "HEAD", "OPTIONS"].includes(method)) requestHeaders.set("X-CSRF-Token", csrfValue);
      return fetch(`${API}${path}`, { ...options, method, headers: requestHeaders, credentials: "include" });
    };
    let response = await send(csrfOverride);
    if (response.status === 401 && !path.startsWith("/auth/")) {
      setAuthenticated(false);
      try { await refreshCsrfToken(); } catch { /* The sign-in view will surface the next auth error. */ }
    }
    if (response.status === 403 && !path.startsWith("/auth/")) {
      const detail = (await response.clone().json().catch(() => ({}))).detail || "";
      if (detail.toLowerCase().includes("csrf")) {
        try { response = await send(await refreshCsrfToken()); } catch { /* Keep the original server response. */ }
      }
    }
    return response;
  }

  async function handleLogin(event) {
    event.preventDefault();
    if (authMode === "register" && loginPassword !== confirmPassword) {
      setLoginError("The passwords do not match.");
      return;
    }
    setAuthLoading(true);
    setLoginError("");
    try {
      const freshCsrf = await refreshCsrfToken();
      const response = await apiFetch(authMode === "register" ? "/auth/register" : "/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: loginName, password: loginPassword }),
      }, freshCsrf);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Login failed.");
      setLoginPassword("");
      setConfirmPassword("");
      setAuthenticated(true);
      await loadDocuments();
      await loadMaterials();
    } catch (err) {
      setLoginError(err.message || "Login failed.");
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleLogout() {
    try {
      const freshCsrf = await refreshCsrfToken();
      await apiFetch("/auth/logout", { method: "POST" }, freshCsrf);
    } finally {
      setDocuments([]);
      setSelectedSources(new Set());
      setAnswer(null);
      setNotesResult(null);
      setFlashcards([]);
      setQuiz(null);
      setQuizResult(null);
      setSavedMaterials([]);
      setLoginName("");
      setLoginPassword("");
      setConfirmPassword("");
      setAuthMode("login");
      try {
        const response = await fetch(`${API}/auth/csrf`, { credentials: "include" });
        if (response.ok) setCsrfToken((await response.json()).csrf_token);
      } catch {
        setCsrfToken("");
        setLoginError("Could not refresh the secure sign-in token. Reload and try again.");
      }
      setAuthenticated(false);
    }
  }

  async function loadDocuments() {
    try {
      const response = await apiFetch("/documents");
      if (!response.ok) throw new Error("Could not load documents.");
      const data = await response.json();
      const nextDocuments = data.documents ?? [];
      setSelectedSources((current) => {
        if (current === null) return new Set(nextDocuments.map((doc) => doc.filename));
        const existing = new Set(documents.map((doc) => doc.filename));
        const next = new Set([...current].filter((filename) => nextDocuments.some((doc) => doc.filename === filename)));
        nextDocuments.forEach((doc) => { if (!existing.has(doc.filename)) next.add(doc.filename); });
        return next;
      });
      setDocuments(nextDocuments);
      setError("");
    } catch {
      setError("Cannot connect to FastAPI. Make sure the backend is running.");
    }
  }

  async function loadMaterials() {
    try {
      const response = await apiFetch("/materials");
      if (!response.ok) throw new Error("Could not restore saved study materials.");
      setSavedMaterials((await response.json()).materials ?? []);
    } catch {
      setError("Could not restore your saved study materials.");
    }
  }

  function openSavedMaterial(material) {
    if (material.kind === "notes") {
      setNotesResult({ ...material.data, id: material.id });
      setActiveTab("Notes");
    } else if (material.kind === "flashcards") {
      setFlashcards(material.data.cards ?? []);
      setCardIndex(0);
      setCardFlipped(false);
      setActiveTab("Flashcards");
    } else if (material.kind === "quiz") {
      const result = material.data;
      setQuiz({ quiz_id: result.quiz_id, questions: result.questions });
      setQuizResult(result.score == null ? null : result);
      setQuizAnswers({});
      setQuizIndex(0);
      setActiveTab("Quiz");
    }
  }

  function chooseFile(nextFile) {
    if (!nextFile) return;
    if (!nextFile.name.toLowerCase().endsWith(".pdf") || (nextFile.type && nextFile.type !== "application/pdf")) {
      setError("Please choose a PDF file.");
      return;
    }
    setError("");
    setMessage("");
    setFile(nextFile);
  }

  function toggleSource(filename) {
    setSelectedSources((current) => {
      const next = new Set(current ?? documents.map((doc) => doc.filename));
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  }

  function selectAllSources() {
    setSelectedSources(new Set(documents.map((doc) => doc.filename)));
  }

  async function uploadPDF(event) {
    event?.preventDefault();
    if (!file) {
      setError("Choose a PDF file first.");
      fileInput.current?.click();
      return;
    }
    setLoading(true); setError(""); setMessage("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await apiFetch("/upload-pdf", { method: "POST", body: formData });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "PDF upload failed.");
      setMessage(`${data.filename} uploaded and indexed (${data.total_pages_extracted} pages).`);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      await loadDocuments();
    } catch (err) {
      setError(err.message || "Upload failed.");
    } finally { setLoading(false); }
  }

  async function deleteDocument(filename) {
    if (!window.confirm(`Delete “${filename}” from your library?`)) return;
    setBusyDocument(filename); setError(""); setMessage("");
    try {
      const response = await apiFetch(`/documents/${encodeURIComponent(filename)}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not delete document.");
      setMessage(`${filename} deleted.`);
      await loadDocuments();
    } catch (err) { setError(err.message || "Delete failed."); }
    finally { setBusyDocument(""); }
  }

  async function runAssistant(event) {
    event?.preventDefault();
    if (!question.trim()) return;
    if (!selectedSources?.size) { setError("Select at least one source before asking the assistant."); return; }
    setWorking(true); setError(""); setAnswer(null);
    try {
      const response = await apiFetch("/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question.trim(), top_k: 6, sources: [...selectedSources] }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "The assistant could not answer.");
      setAnswer(data);
    } catch (err) { setError(err.message || "Request failed."); }
    finally { setWorking(false); }
  }

  const filteredDocuments = documents.filter((doc) => doc.filename.toLowerCase().includes(filter.toLowerCase()));
  const totalChunks = documents.reduce((total, doc) => total + (doc.chunks || 0), 0);
  async function requestStudy(route, payload, onSuccess, requiresSources = true) {
    if (requiresSources && !selectedSources?.size) {
      setError("Select at least one source before generating study material.");
      return;
    }
    setWorking(true);
    setError("");
    try {
      const response = await apiFetch(route, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, ...(requiresSources ? { sources: [...selectedSources] } : {}) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not generate study material.");
      onSuccess(data);
      await loadMaterials();
    } catch (err) {
      setError(err.message || "Could not generate study material.");
    } finally {
      setWorking(false);
    }
  }

  function createNotes() {
    setNotesResult(null);
    requestStudy("/notes/generate", { style: notesStyle, detail_level: notesDetail }, setNotesResult);
  }

  function createFlashcards() {
    setFlashcards([]);
    setCardIndex(0);
    setCardFlipped(false);
    requestStudy("/flashcards/generate", { card_count: cardCount, difficulty: studyDifficulty }, (data) => setFlashcards(data.cards ?? []));
  }

  function shuffleFlashcards() {
    setFlashcards((current) => {
      const shuffled = [...current];
      for (let index = shuffled.length - 1; index > 0; index -= 1) {
        const swapIndex = Math.floor(Math.random() * (index + 1));
        [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
      }
      return shuffled;
    });
    setCardIndex(0);
    setCardFlipped(false);
  }

  function createQuiz() {
    setQuiz(null);
    setQuizResult(null);
    setQuizAnswers({});
    setQuizIndex(0);
    requestStudy("/quiz/generate", { question_count: quizCount, difficulty: studyDifficulty, question_type: "multiple_choice" }, (data) => setQuiz(data));
  }

  function submitQuiz() {
    if (!quiz) return;
    requestStudy("/quiz/submit", { quiz_id: quiz.quiz_id, answers: quizAnswers }, setQuizResult, false);
  }

  if (!authReady) return <div className="auth-loading"><span className="brand-icon">N</span><span>Loading your private workspace...</span></div>;
  if (!authenticated) return <LoginPage
    mode={authMode} setMode={(mode) => { setAuthMode(mode); setLoginError(""); }}
    username={loginName} setUsername={setLoginName}
    password={loginPassword} setPassword={setLoginPassword}
    passwordVisible={loginPasswordVisible} setPasswordVisible={setLoginPasswordVisible}
    confirmPassword={confirmPassword} setConfirmPassword={setConfirmPassword}
    confirmPasswordVisible={confirmPasswordVisible} setConfirmPasswordVisible={setConfirmPasswordVisible}
    error={loginError} loading={authLoading} onSubmit={handleLogin}
  />;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#sources" onClick={(event) => { event.preventDefault(); setActiveTab("Sources"); }}>
          <span className="brand-icon">N</span><span>Mini NotebookLM</span>
        </a>
        <nav className="navigation" aria-label="Workspace">
          {tabs.map((tab) => <button key={tab} className={`nav-tab ${activeTab === tab ? "active" : ""}`} onClick={() => { setActiveTab(tab); setAnswer(null); }}>{tab}</button>)}
        </nav>
        <div className="connection">{error.includes("connect") ? "Backend offline" : "Personal workspace"}<button className="logout-button" onClick={handleLogout}>Log out</button></div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading"><div><span className="eyebrow">YOUR WORKSPACE</span><h2>Source Deck</h2></div><span className="count-badge">{documents.length}</span></div>
          <div className="library-heading"><span>CHAT SOURCES · {selectedSources?.size ?? 0} SELECTED</span><button className="select-all-button" onClick={selectAllSources} disabled={selectedSources?.size === documents.length}>Select all</button></div>
          <label className="search-wrap"><span aria-hidden="true"><Icon name="search" /></span><input className="search-input" aria-label="Search documents" placeholder="Search documents" value={filter} onChange={(event) => setFilter(event.target.value)} /></label>
          <div className="document-list">
            {filteredDocuments.map((doc) => <div className={`document-item ${selectedSources?.has(doc.filename) ? "selected" : ""}`} key={doc.document_id}><label className="source-check" title={`Use ${doc.filename} in Chat and study tools`}><input type="checkbox" checked={selectedSources?.has(doc.filename) ?? false} onChange={() => toggleSource(doc.filename)} aria-label={`Select ${doc.filename} for Chat`} /></label><span className="pdf-icon">PDF</span><div className="document-info"><strong title={doc.filename}>{doc.filename}</strong><span>{doc.pages} pages · {doc.chunks} chunks</span></div>{doc.download_available && <a className="document-action" href={`${API}/documents/${encodeURIComponent(doc.filename)}/download`} aria-label={`Download ${doc.filename}`} title="Download PDF"><Icon name="download" /></a>}<button className="delete-button" title={`Delete ${doc.filename}`} disabled={busyDocument === doc.filename} onClick={() => deleteDocument(doc.filename)}><Icon name="trash" /></button></div>)}
            {filteredDocuments.length === 0 && <p className="empty-documents">{documents.length ? "No documents match your search." : "Your library is empty. Upload a PDF to begin."}</p>}
          </div>
          <div className="sidebar-footer"><span>VECTOR DATABASE</span><strong>ChromaDB</strong><p>{totalChunks} indexed chunks across your documents</p></div>
        </aside>

        <main className="main-content">
          {(error || message) && <div className={`notice ${error ? "error-notice" : "success-notice"}`} role="status">{error || message}<button aria-label="Dismiss notice" onClick={() => { setError(""); setMessage(""); }}>×</button></div>}
          {activeTab === "Sources" ? <>
            <section className="welcome-banner"><div><span className="eyebrow">YOUR KNOWLEDGE WORKSPACE</span><h1>Source Vault</h1><p>Bring your PDFs together and turn them into a searchable knowledge base for learning and research.</p></div><span className="banner-symbol" aria-hidden="true"><Icon name="sparkle" /></span></section>
            <section className="stats-grid"><div className="stat-card"><span>Documents</span><strong>{documents.length}</strong><small>PDFs in your library</small></div><div className="stat-card"><span>Indexed chunks</span><strong>{totalChunks}</strong><small>Available for retrieval</small></div><div className="stat-card"><span>Knowledge base</span><strong>ChromaDB</strong><small>Persistent vector storage</small></div></section>
            <section className="content-card"><div className="section-heading"><div><h2>Manage your sources</h2><p>Upload a PDF or drop it here. Select sources in the deck for Chat and study tools.</p></div></div>
              <form className="drop-area" onSubmit={uploadPDF} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files?.[0]); }}><input ref={fileInput} className="visually-hidden" type="file" accept=".pdf,application/pdf" onChange={(event) => chooseFile(event.target.files?.[0])} /><span className="large-upload-icon" aria-hidden="true"><Icon name="upload" /></span><h3>{file ? "PDF ready to upload" : "Add a source PDF"}</h3><p>{file ? "Your selected document will be added to the source deck." : "Drop a PDF here or choose a file from your device."}</p>{file && <strong className="selected-file main-selected-file" title={file.name}>{file.name}<button type="button" aria-label="Clear selected PDF" onClick={() => { setFile(null); if (fileInput.current) fileInput.current.value = ""; }}>×</button></strong>}<div className="drop-actions"><button type="button" className="secondary-button" onClick={() => fileInput.current?.click()}>{file ? "Choose another PDF" : "Choose PDF"}</button><button className="primary-button" disabled={loading || !file}>{loading ? "Processing PDF…" : "Upload PDF"}</button></div><small>PDF files only</small></form>
            </section>
          </> : activeTab === "Chat" ? <>
            <section className="page-heading"><span className="eyebrow">GROUNDED RESEARCH</span><h1>Ask your sources</h1><p>Get answers based on your uploaded PDFs, with page references for follow-up.</p></section>
            <section className="content-card assistant-card"><div className="assistant-intro"><span className="assistant-mark">✦</span><div><h2>Research assistant</h2><p>{selectedSources?.size ? `${selectedSources.size} of ${documents.length} sources selected` : "Select sources in the deck to start a grounded conversation"}</p></div></div>
              {answer && <Answer answer={answer} />}
              <form className="prompt-form" onSubmit={runAssistant}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about your uploaded research PDFs…" rows={3} /><div className="prompt-footer"><span>{documents.length} source{documents.length === 1 ? "" : "s"} · Answers include citations</span><button className="primary-button" disabled={working || !question.trim() || !selectedSources?.size}>{working ? "Thinking…" : "Ask question →"}</button></div></form>
            </section>
          </> : activeTab === "Notes" ? <NotesStudio
            selectedCount={selectedSources?.size ?? 0}
            style={notesStyle} setStyle={setNotesStyle}
            detail={notesDetail} setDetail={setNotesDetail}
            onGenerate={createNotes} loading={working} result={notesResult}
            saved={savedMaterials.filter((item) => item.kind === "notes")} onOpenSaved={openSavedMaterial}
          /> : activeTab === "Flashcards" ? <FlashcardStudio
            selectedCount={selectedSources?.size ?? 0}
            count={cardCount} setCount={setCardCount}
            difficulty={studyDifficulty} setDifficulty={setStudyDifficulty}
            onGenerate={createFlashcards} loading={working}
            cards={flashcards} index={cardIndex} setIndex={setCardIndex}
            flipped={cardFlipped} setFlipped={setCardFlipped}
            onShuffle={shuffleFlashcards}
            saved={savedMaterials.filter((item) => item.kind === "flashcards")} onOpenSaved={openSavedMaterial}
          /> : <QuizStudio
            selectedCount={selectedSources?.size ?? 0}
            count={quizCount} setCount={setQuizCount}
            difficulty={studyDifficulty} setDifficulty={setStudyDifficulty}
            onGenerate={createQuiz} loading={working} quiz={quiz}
            index={quizIndex} setIndex={setQuizIndex}
            answers={quizAnswers} setAnswers={setQuizAnswers}
            onSubmit={submitQuiz} result={quizResult}
            saved={savedMaterials.filter((item) => item.kind === "quiz")} onOpenSaved={openSavedMaterial}
          />}
        </main>
      </div>
    </div>
  );
}



function LoginPage({ mode, setMode, username, setUsername, password, setPassword, passwordVisible, setPasswordVisible, confirmPassword, setConfirmPassword, confirmPasswordVisible, setConfirmPasswordVisible, error, loading, onSubmit }) {
  const isSignup = mode === "register";
  return <main className="login-screen">
    <section className="login-card">
      <div className="login-art" aria-hidden="true">
        <span className="brand-icon">N</span>
        <div className="login-document"><span /><span /><span /></div>
        <p>Your personal learning<br />workspace starts here.</p>
      </div>
      <div className="login-panel">
        <span className="eyebrow">MINI NOTEBOOKLM</span>
        <h1>{isSignup ? "Create your account" : "Welcome back"}</h1>
        <p className="login-intro">{isSignup ? "Keep your documents and study materials together." : "Sign in to continue to your personal workspace."}</p>
        <form className="login-form" onSubmit={onSubmit}>
          <label htmlFor="account-identifier">Email or username</label>
          <input id="account-identifier" type="text" autoComplete={isSignup ? "username" : "username"} value={username} onChange={(event) => setUsername(event.target.value)} maxLength={254} required />
          <label htmlFor="account-password">Password</label>
          <div className="password-input-wrap"><input id="account-password" type={passwordVisible ? "text" : "password"} autoComplete={isSignup ? "new-password" : "current-password"} minLength={isSignup ? 6 : undefined} value={password} onChange={(event) => setPassword(event.target.value)} required /><button type="button" className="password-toggle" aria-label={passwordVisible ? "Hide password" : "Show password"} aria-pressed={passwordVisible} onClick={() => setPasswordVisible((value) => !value)}>{passwordVisible ? "Hide" : "Show"}</button></div>
          {isSignup && <><label htmlFor="confirm-password">Confirm password</label><div className="password-input-wrap"><input id="confirm-password" type={confirmPasswordVisible ? "text" : "password"} autoComplete="new-password" minLength={6} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /><button type="button" className="password-toggle" aria-label={confirmPasswordVisible ? "Hide confirmation" : "Show confirmation"} aria-pressed={confirmPasswordVisible} onClick={() => setConfirmPasswordVisible((value) => !value)}>{confirmPasswordVisible ? "Hide" : "Show"}</button></div></>}
          {error && <p className="login-error" role="alert">{error}</p>}
          <button className="primary-button login-submit" type="submit" disabled={loading || !username || !password || (isSignup && !confirmPassword)}>{loading ? (isSignup ? "Creating account..." : "Signing in...") : (isSignup ? "Create account" : "Sign in")}</button>
        </form>
        <p className="auth-switch">{isSignup ? "Already have an account?" : "New to Mini NotebookLM?"}<button type="button" onClick={() => setMode(isSignup ? "login" : "register")}>{isSignup ? "Sign in" : "Create an account"}</button></p>
      </div>
    </section>
  </main>;
}

function StudyHistory({ materials, onOpen }) {
  if (!materials?.length) return null;
  return <section className="study-history" aria-label="Saved study materials">
    <span className="eyebrow">SAVED MATERIALS</span>
    <div className="study-history-list">{materials.map((item) => <button key={item.id} className="study-history-item" onClick={() => onOpen(item)}><span>{item.title}</span><time dateTime={new Date(item.created_at * 1000).toISOString()}>{new Date(item.created_at * 1000).toLocaleDateString()}</time></button>)}</div>
  </section>;
}

function StudySourcesSummary({ count }) {
  return <p className="study-source-summary">{count > 0 ? `${count} selected source${count === 1 ? "" : "s"} will be used.` : "Select one or more sources in the Source Deck to continue."}</p>;
}

function StudyCitations({ sources = [] }) {
  if (!sources.length) return null;
  return <div className="study-citations"><span>Sources</span>{sources.map((source, index) => <span className="citation" key={`${source.filename}-${source.page_number}-${index}`}>{source.filename}{source.page_number != null ? ` · p. ${source.page_number}` : ""}</span>)}</div>;
}

function MarkdownContent({ children }) {
  return <div className="answer-markdown">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        table: ({ node, children: tableChildren, ...props }) => <div className="markdown-table-wrap"><table {...props}>{tableChildren}</table></div>,
        a: ({ node, href = "", children: linkChildren, ...props }) => <a href={href} target="_blank" rel="noreferrer" {...props}>{linkChildren}</a>,
      }}
    >{children}</ReactMarkdown>
  </div>;
}

function StudyControls({ selectedCount, loading, onGenerate, children, label }) {
  return <div className="content-card study-card">
    <div className="study-controls-heading"><div><h2>Build from selected sources</h2><StudySourcesSummary count={selectedCount} /></div></div>
    <div className="study-controls-row">{children}<button className="primary-button" onClick={onGenerate} disabled={loading || selectedCount === 0}>{loading ? "Generating..." : label}</button></div>
  </div>;
}

function NotesStudio({ selectedCount, style, setStyle, detail, setDetail, onGenerate, loading, result, saved, onOpenSaved }) {
  return <>
    <section className="page-heading"><span className="eyebrow">STUDY STUDIO</span><h1>AI notes &amp; study guide</h1><p>Build a structured set of revision notes from the complete text of your selected PDFs.</p></section>
    <StudyHistory materials={saved} onOpen={onOpenSaved} />
    <StudyControls selectedCount={selectedCount} loading={loading} onGenerate={onGenerate} label="Generate notes">
      <label className="study-field"><span>Notes style</span><select className="study-select" value={style} onChange={(event) => setStyle(event.target.value)}><option value="study_guide">Study guide</option><option value="summary">Summary</option><option value="detailed">Detailed notes</option></select></label>
      <label className="study-field"><span>Detail</span><select className="study-select" value={detail} onChange={(event) => setDetail(event.target.value)}><option value="concise">Concise</option><option value="balanced">Balanced</option><option value="thorough">Thorough</option></select></label>
    </StudyControls>
    {!result ? <section className="content-card study-empty"><span className="tool-empty-mark"><Icon name="notes" /></span><h2>Your notes will appear here</h2><p>Choose a style and generate notes grounded in every selected document.</p></section> : <article className="content-card notes-result">
      <span className="eyebrow">GENERATED STUDY NOTES</span><h2>{result.title}</h2><p className="notes-overview">{result.overview}</p>
      {result.sections.map((section, index) => <section className="notes-section" key={`${section.heading}-${index}`}><h3>{section.heading}</h3><MarkdownContent>{section.markdown}</MarkdownContent><StudyCitations sources={section.sources} /></section>)}
      {result.quick_revision?.length > 0 && <section className="quick-revision"><h3>Quick revision</h3><ul>{result.quick_revision.map((point, index) => <li key={`${index}-${point}`}>{point}</li>)}</ul></section>}
    </article>}
  </>;
}

function FlashcardStudio({ selectedCount, count, setCount, difficulty, setDifficulty, onGenerate, loading, cards, index, setIndex, flipped, setFlipped, onShuffle, saved, onOpenSaved }) {
  const card = cards[index];
  function moveTo(nextIndex) {
    setIndex(nextIndex);
    setFlipped(false);
  }
  return <>
    <section className="page-heading"><span className="eyebrow">ACTIVE RECALL</span><h1>Flashcard review</h1><p>Practice concepts and definitions drawn from all selected source pages.</p></section>
    <StudyHistory materials={saved} onOpen={onOpenSaved} />
    <StudyControls selectedCount={selectedCount} loading={loading} onGenerate={onGenerate} label="Create flashcards">
      <label className="study-field"><span>Number of cards</span><select className="study-select" value={count} onChange={(event) => setCount(Number(event.target.value))}>{[8, 12, 20, 30].map((value) => <option value={value} key={value}>{value} cards</option>)}</select></label>
      <label className="study-field"><span>Difficulty</span><select className="study-select" value={difficulty} onChange={(event) => setDifficulty(event.target.value)}><option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option></select></label>
    </StudyControls>
    {!card ? <section className="content-card study-empty"><span className="tool-empty-mark"><Icon name="cards" /></span><h2>Your cards will appear here</h2><p>Generate a deck to begin reviewing. Answers stay hidden until you flip a card.</p></section> : <section className="content-card flashcard-session">
      <div className="flashcard-toolbar"><span className="eyebrow">CARD {index + 1} OF {cards.length}</span><button className="secondary-button" onClick={onShuffle}>Shuffle deck</button></div>
      <button className={`flashcard ${flipped ? "is-flipped" : ""}`} onClick={() => setFlipped((value) => !value)} aria-label={flipped ? "Show question" : "Reveal answer"}>
        <span className="flashcard-face-label">{flipped ? "ANSWER" : "QUESTION"}</span>
        <span className="flashcard-content">{flipped ? card.answer : card.question}</span>
        <span className="flashcard-hint">{flipped ? "Click to return to question" : "Click or tap to reveal answer"}</span>
      </button>
      <StudyCitations sources={card.sources} />
      <div className="flashcard-navigation"><button className="secondary-button" onClick={() => moveTo(index - 1)} disabled={index === 0}>Previous</button><span>{index + 1} / {cards.length}</span><button className="secondary-button" onClick={() => moveTo(index + 1)} disabled={index === cards.length - 1}>Next</button></div>
      <button className="text-action" onClick={() => moveTo(0)}>Restart deck</button>
    </section>}
  </>;
}

function QuizStudio({ selectedCount, count, setCount, difficulty, setDifficulty, onGenerate, loading, quiz, index, setIndex, answers, setAnswers, onSubmit, result, saved, onOpenSaved }) {
  const current = quiz?.questions?.[index];
  return <>
    <section className="page-heading"><span className="eyebrow">KNOWLEDGE CHECK</span><h1>AI quiz &amp; self-assessment</h1><p>Answer questions created from the full selected documents. Correct answers stay hidden until you submit.</p></section>
    <StudyHistory materials={saved} onOpen={onOpenSaved} />
    <StudyControls selectedCount={selectedCount} loading={loading} onGenerate={onGenerate} label={quiz ? "Generate another quiz" : "Generate quiz"}>
      <label className="study-field"><span>Number of questions</span><select className="study-select" value={count} onChange={(event) => setCount(Number(event.target.value))}>{[5, 10, 15, 20].map((value) => <option value={value} key={value}>{value} questions</option>)}</select></label>
      <label className="study-field"><span>Difficulty</span><select className="study-select" value={difficulty} onChange={(event) => setDifficulty(event.target.value)}><option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option></select></label>
    </StudyControls>
    {!quiz ? <section className="content-card study-empty"><span className="tool-empty-mark"><Icon name="quiz" /></span><h2>Your quiz will appear here</h2><p>Generate a quiz to check your understanding of the selected sources.</p></section> : result ? <section className="content-card quiz-results">
      <div className="quiz-score"><span className="eyebrow">QUIZ RESULTS</span><strong>{result.score} / {result.total_questions}</strong><span>{result.percentage}% correct</span></div>
      {result.questions.map((item, questionIndex) => <article className={`quiz-result-item ${item.is_correct ? "correct" : "incorrect"}`} key={item.id}>
        <h3>{questionIndex + 1}. {item.question}</h3>
        <p className="quiz-answer-line">Your answer: {item.options.find((option) => option.id === item.selected_option_id)?.text}</p>
        {!item.is_correct && <p className="quiz-answer-line">Correct answer: {item.options.find((option) => option.id === item.correct_option_id)?.text}</p>}
        <p>{item.explanation}</p><StudyCitations sources={item.sources} />
      </article>)}
      <button className="primary-button" onClick={onGenerate} disabled={loading}>Generate another quiz</button>
    </section> : <section className="content-card quiz-session">
      <div className="quiz-progress-heading"><span>Question {index + 1} of {quiz.questions.length}</span><span>{Object.keys(answers).length} answered</span></div>
      <div className="quiz-progress"><span style={{ width: `${((index + 1) / quiz.questions.length) * 100}%` }} /></div>
      <h2>{current.question}</h2>
      <div className="quiz-options">{current.options.map((option) => <button key={option.id} className={`quiz-option ${answers[current.id] === option.id ? "selected" : ""}`} onClick={() => setAnswers((previous) => ({ ...previous, [current.id]: option.id }))}><span>{option.id}</span>{option.text}</button>)}</div>
      <StudyCitations sources={current.sources} />
      <div className="quiz-navigation"><button className="secondary-button" onClick={() => setIndex((value) => value - 1)} disabled={index === 0}>Previous</button><span>{index + 1} / {quiz.questions.length}</span>{index < quiz.questions.length - 1 ? <button className="secondary-button" onClick={() => setIndex((value) => value + 1)}>Next</button> : <button className="primary-button" onClick={onSubmit} disabled={loading || Object.keys(answers).length !== quiz.questions.length}>{loading ? "Submitting..." : "Submit quiz"}</button>}</div>
    </section>}
  </>;
}

function Icon({ name }) {
  const shapes = {
    search: <><circle cx="10.8" cy="10.8" r="6.3" /><path d="m15.5 15.5 4.1 4.1" /></>,
    upload: <><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" /><path d="M4.5 14.5v4A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5v-4" /></>,
    download: <><path d="M12 4v12m0 0 4.5-4.5M12 16l-4.5-4.5" /><path d="M4.5 16v3A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5v-3" /></>,
    trash: <><path d="M4 7h16M9 7V4.5h6V7m3 0-.8 13H6.8L6 7" /><path d="M10 10.5v6m4-6v6" /></>,
    sparkle: <><path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2Z" /><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z" /></>,
    notes: <><path d="M6 3.5h8l4 4V20H6z" /><path d="M14 3.5V8h4M9 12h6M9 15.5h6" /></>,
    cards: <><rect x="5" y="5" width="13" height="15" rx="2" /><path d="M8 3h9a2 2 0 0 1 2 2v13" /></>,
    quiz: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16.5 9" /></>,
  };
  return <svg className={`ui-icon icon-${name}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}

function sourceAnchorId(source) {
  const slug = encodeURIComponent(source.filename).replaceAll("%", "_");
  return `answer-source-${slug}-page-${source.page_number}`;
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function remarkCitations(sources = []) {
  const citationPatterns = sources
    .filter((source) => source.filename && source.page_number != null)
    .map((source) => ({
      source,
      pattern: new RegExp(`\\[${escapeRegExp(source.filename)}\\s*,\\s*p\\.\\s*${escapeRegExp(String(source.page_number))}\\]`, "i"),
    }));

  return () => (tree) => {
    function visit(node) {
      if (["code", "inlineCode", "link", "linkReference"].includes(node.type) || !Array.isArray(node.children)) return;
      const children = [];
      for (const child of node.children) {
        if (child.type !== "text") {
          visit(child);
          children.push(child);
          continue;
        }

        let remaining = child.value;
        while (remaining) {
          const candidates = citationPatterns.flatMap(({ source, pattern }) => {
            pattern.lastIndex = 0;
            const match = pattern.exec(remaining);
            return match ? [{ source, match }] : [];
          }).sort((left, right) => left.match.index - right.match.index);
          const found = candidates[0];
          if (!found) {
            children.push({ type: "text", value: remaining });
            break;
          }
          if (found.match.index > 0) children.push({ type: "text", value: remaining.slice(0, found.match.index) });
          children.push({
            type: "link",
            url: `#${sourceAnchorId(found.source)}`,
            title: `${found.source.filename}, page ${found.source.page_number}`,
            children: [{ type: "text", value: found.match[0] }],
          });
          remaining = remaining.slice(found.match.index + found.match[0].length);
        }
      }
      node.children = children;
    }
    visit(tree);
  };
}

export function Answer({ answer }) {
  const sources = answer.sources ?? [];
  return <article className="answer-card">
    <div className="answer-label">NOTEBOOKLM RESPONSE</div>
    <div className="answer-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitations(sources)]}
        components={{
          table: ({ node, children, ...props }) => <div className="markdown-table-wrap"><table {...props}>{children}</table></div>,
          a: ({ node, href = "", children, ...props }) => href.startsWith("#answer-source-")
            ? <a href={href} {...props}>{children}</a>
            : <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>,
        }}
      >{answer.answer}</ReactMarkdown>
    </div>
    {sources.length > 0 && <div className="citations"><strong>Cited sources</strong>{sources.map((source, index) => <span className="citation" id={sourceAnchorId(source)} key={`${source.filename}-${source.page_number}-${index}`}>{source.filename}{source.page_number != null ? ` \u00b7 p. ${source.page_number}` : ""}</span>)}</div>}
  </article>;
}

