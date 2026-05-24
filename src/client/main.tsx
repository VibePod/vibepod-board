import {
  ArrowRight,
  Check,
  Columns3,
  ExternalLink,
  FileText,
  Github,
  ListChecks,
  Network,
  Pencil,
  Plus,
  PlugZap,
  RefreshCcw,
  Save,
  X
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { endpointOptions, integrationExamples } from "../shared/integrationExamples.js";
import {
  boardColumns,
  documentKinds,
  ideaStatuses,
  type BoardCard,
  type BoardColumn,
  type BoardColumns,
  type DocumentKind,
  type Idea,
  type IdeaStatus,
  type PlanDocument
} from "../shared/types.js";
import vibepodIconUrl from "./assets/icon.png";
import { formatListField, parseListField } from "./formUtils.js";
import "./styles.css";

type AppState = {
  ideas: Idea[];
  columns: BoardColumns;
  documents: PlanDocument[];
};

type TaskDraft = {
  id?: string;
  title: string;
  summary: string;
  details: string;
  labels: string;
  acceptanceCriteria: string;
  status: IdeaStatus;
};

type NoteDraft = {
  id?: string;
  title: string;
  kind: DocumentKind;
  content: string;
  linkedIdeaId: string;
};

type TaskModalState = {
  mode: "create" | "edit";
  draft: TaskDraft;
};

type NoteModalState = {
  mode: "create" | "edit";
  draft: NoteDraft;
};

const emptyColumns: BoardColumns = {
  ready: [],
  planned: [],
  in_progress: [],
  review: [],
  done: []
};

const columnLabels: Record<BoardColumn, string> = {
  ready: "Ready",
  planned: "Planned",
  in_progress: "In Progress",
  review: "Review",
  done: "Done"
};

const nextColumn: Partial<Record<BoardColumn, BoardColumn>> = {
  ready: "planned",
  planned: "in_progress",
  in_progress: "review",
  review: "done"
};

const emptyTaskDraft = (): TaskDraft => ({
  title: "",
  summary: "",
  details: "",
  labels: "",
  acceptanceCriteria: "",
  status: "idea"
});

const emptyNoteDraft = (): NoteDraft => ({
  title: "",
  kind: "notes",
  content: "",
  linkedIdeaId: ""
});

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    }
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(error.error ?? `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
};

const App = () => {
  const [state, setState] = useState<AppState>({
    ideas: [],
    columns: emptyColumns,
    documents: []
  });
  const [activeView, setActiveView] = useState<"ideas" | "board" | "documents">("ideas");
  const [taskModal, setTaskModal] = useState<TaskModalState | null>(null);
  const [noteModal, setNoteModal] = useState<NoteModalState | null>(null);
  const [error, setError] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [isMcpGuideOpen, setIsMcpGuideOpen] = useState(false);

  const loadState = async () => {
    setError("");
    const [ideas, board, documents] = await Promise.all([
      api<{ items: Idea[] }>("/api/ideas"),
      api<{ columns: BoardColumns }>("/api/board"),
      api<{ items: PlanDocument[] }>("/api/documents")
    ]);
    setState({
      ideas: ideas.items,
      columns: board.columns,
      documents: documents.items
    });
    setIsLoading(false);
  };

  useEffect(() => {
    loadState().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load board");
      setIsLoading(false);
    });
  }, []);

  useEffect(() => {
    if (!isMcpGuideOpen && !taskModal && !noteModal) {
      return;
    }

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsMcpGuideOpen(false);
        setTaskModal(null);
        setNoteModal(null);
      }
    };

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isMcpGuideOpen, taskModal, noteModal]);

  const openCreateTask = () => {
    setTaskModal({ mode: "create", draft: emptyTaskDraft() });
  };

  const openEditTask = (idea: Idea) => {
    setTaskModal({
      mode: "edit",
      draft: {
        id: idea.id,
        title: idea.title,
        summary: idea.summary,
        details: idea.details,
        labels: formatListField(idea.labels),
        acceptanceCriteria: formatListField(idea.acceptanceCriteria),
        status: idea.status
      }
    });
  };

  const updateTaskDraft = (patch: Partial<TaskDraft>) => {
    setTaskModal((current) =>
      current ? { ...current, draft: { ...current.draft, ...patch } } : current
    );
  };

  const saveTask = async (event: FormEvent) => {
    event.preventDefault();
    if (!taskModal) {
      return;
    }
    setError("");
    const payload = {
      title: taskModal.draft.title,
      summary: taskModal.draft.summary,
      details: taskModal.draft.details,
      labels: parseListField(taskModal.draft.labels),
      acceptanceCriteria: parseListField(taskModal.draft.acceptanceCriteria)
    };

    if (taskModal.mode === "create") {
      const created = await api<{ item: Idea }>("/api/ideas", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      if (taskModal.draft.status !== "idea") {
        await api(`/api/ideas/${created.item.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: taskModal.draft.status })
        });
      }
    } else if (taskModal.draft.id) {
      await api(`/api/ideas/${taskModal.draft.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...payload,
          status: taskModal.draft.status
        })
      });
    }
    setTaskModal(null);
    await loadState();
  };

  const openCreateNote = () => {
    setNoteModal({ mode: "create", draft: emptyNoteDraft() });
  };

  const openEditNote = (document: PlanDocument) => {
    setNoteModal({
      mode: "edit",
      draft: {
        id: document.id,
        title: document.title,
        kind: document.kind,
        content: document.content,
        linkedIdeaId: document.linkedIdeaIds[0] ?? ""
      }
    });
  };

  const updateNoteDraft = (patch: Partial<NoteDraft>) => {
    setNoteModal((current) =>
      current ? { ...current, draft: { ...current.draft, ...patch } } : current
    );
  };

  const markReady = async (ideaId: string) => {
    setError("");
    await api(`/api/ideas/${ideaId}/ready`, { method: "POST" });
    await loadState();
  };

  const syncToBoard = async (ideaId: string) => {
    setError("");
    await api(`/api/ideas/${ideaId}/sync-github`, {
      method: "POST",
      body: JSON.stringify({})
    });
    setActiveView("board");
    await loadState();
  };

  const moveCard = async (card: BoardCard, column: BoardColumn) => {
    setError("");
    await api(`/api/board/${card.id}`, {
      method: "PATCH",
      body: JSON.stringify({ column })
    });
    await loadState();
  };

  const saveNote = async (event: FormEvent) => {
    event.preventDefault();
    if (!noteModal) {
      return;
    }
    setError("");
    const payload = {
      title: noteModal.draft.title,
      content: noteModal.draft.content,
      kind: noteModal.draft.kind,
      linkedIdeaIds: noteModal.draft.linkedIdeaId ? [noteModal.draft.linkedIdeaId] : []
    };

    if (noteModal.mode === "create") {
      await api("/api/documents", {
        method: "POST",
        body: JSON.stringify(payload)
      });
    } else if (noteModal.draft.id) {
      await api(`/api/documents/${noteModal.draft.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload)
      });
    }
    setNoteModal(null);
    await loadState();
  };

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <img src={vibepodIconUrl} alt="" />
          </div>
          <div>
            <h1>vibepod-board</h1>
            <p>Tasks, board, notes, MCP.</p>
          </div>
        </div>
        <nav className="nav">
          <button className={activeView === "ideas" ? "active" : ""} onClick={() => setActiveView("ideas")}>
            <ListChecks size={18} />
            Tasks
          </button>
          <button className={activeView === "board" ? "active" : ""} onClick={() => setActiveView("board")}>
            <Columns3 size={18} />
            Board
          </button>
          <button
            className={activeView === "documents" ? "active" : ""}
            onClick={() => setActiveView("documents")}
          >
            <FileText size={18} />
            Notes
          </button>
        </nav>
        <button
          type="button"
          className="mcp-box"
          aria-haspopup="dialog"
          onClick={() => setIsMcpGuideOpen(true)}
        >
          <span>
            <PlugZap size={15} />
            MCP endpoint
          </span>
          <code>/mcp</code>
        </button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h2>{activeView === "ideas" ? "Tasks" : activeView === "board" ? "Kanban Board" : "Notes"}</h2>
            <p>
              {state.ideas.length} tasks, {countCards(state.columns)} cards, {state.documents.length} notes
            </p>
          </div>
          <div className="topbar-actions">
            {activeView === "ideas" && (
              <button className="primary" type="button" onClick={openCreateTask}>
                <Plus size={18} />
                Add Task
              </button>
            )}
            {activeView === "documents" && (
              <button className="primary" type="button" onClick={openCreateNote}>
                <Plus size={18} />
                Add Note
              </button>
            )}
            <button className="icon-button" aria-label="Refresh" onClick={() => loadState()}>
              <RefreshCcw size={18} />
            </button>
          </div>
        </header>

        {error && <div className="error">{error}</div>}
        {isLoading ? <div className="loading">Loading board...</div> : null}

        {activeView === "ideas" && (
          <div className="list">
            {state.ideas.map((idea) => (
              <article className="item" key={idea.id}>
                <div className="item-head">
                  <h3>{idea.title}</h3>
                  <span className={`status ${idea.status}`}>{idea.status}</span>
                </div>
                <p>{idea.summary || idea.details || "No details yet."}</p>
                <div className="labels">
                  {idea.labels.map((label) => (
                    <span key={label}>{label}</span>
                  ))}
                </div>
                <div className="actions">
                  <button type="button" onClick={() => openEditTask(idea)}>
                    <Pencil size={16} />
                    Edit
                  </button>
                  <button type="button" onClick={() => void markReady(idea.id)}>
                    <Check size={16} />
                    Ready
                  </button>
                  <button type="button" onClick={() => void syncToBoard(idea.id)}>
                    <Github size={16} />
                    Sync
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}

        {activeView === "board" && (
          <div className="board">
            {boardColumns.map((column) => (
              <section className="column" key={column}>
                <div className="column-head">
                  <h3>{columnLabels[column]}</h3>
                  <span>{state.columns[column]?.length ?? 0}</span>
                </div>
                <div className="cards">
                  {(state.columns[column] ?? []).map((card) => (
                    <article className="card" key={card.id}>
                      <h4>{card.title}</h4>
                      <p>{card.details || "No details collected."}</p>
                      <div className="labels">
                        {card.labels.map((label) => (
                          <span key={label}>{label}</span>
                        ))}
                      </div>
                      {nextColumn[column] && (
                        <button type="button" onClick={() => void moveCard(card, nextColumn[column]!)}>
                          <ArrowRight size={16} />
                          {columnLabels[nextColumn[column]!]}
                        </button>
                      )}
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        {activeView === "documents" && (
          <div className="list">
            {state.documents.map((document) => (
              <article className="item document" key={document.id}>
                <div className="item-head">
                  <h3>{document.title}</h3>
                  <span className="status notes">{document.kind}</span>
                </div>
                <pre>{document.content || "No note content yet."}</pre>
                <div className="actions">
                  <button type="button" onClick={() => openEditNote(document)}>
                    <Pencil size={16} />
                    Edit
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {taskModal && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setTaskModal(null);
            }
          }}
        >
          <section className="modal compact-modal" role="dialog" aria-modal="true" aria-labelledby="task-dialog-title">
            <header className="modal-header">
              <div>
                <span className="eyebrow">
                  <ListChecks size={15} />
                  Task
                </span>
                <h2 id="task-dialog-title">{taskModal.mode === "create" ? "Add Task" : "Edit Task"}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close task dialog"
                onClick={() => setTaskModal(null)}
              >
                <X size={18} />
              </button>
            </header>

            <form className="modal-form" onSubmit={(event) => void saveTask(event)}>
              <div className="field-grid">
                <label>
                  Title
                  <input
                    value={taskModal.draft.title}
                    onChange={(event) => updateTaskDraft({ title: event.target.value })}
                    required
                  />
                </label>
                <label>
                  Status
                  <select
                    value={taskModal.draft.status}
                    onChange={(event) => updateTaskDraft({ status: event.target.value as IdeaStatus })}
                  >
                    {ideaStatuses.map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="full-span">
                  Summary
                  <textarea
                    value={taskModal.draft.summary}
                    onChange={(event) => updateTaskDraft({ summary: event.target.value })}
                    rows={3}
                  />
                </label>
                <label className="full-span">
                  Details
                  <textarea
                    value={taskModal.draft.details}
                    onChange={(event) => updateTaskDraft({ details: event.target.value })}
                    rows={6}
                  />
                </label>
                <label>
                  Labels
                  <textarea
                    value={taskModal.draft.labels}
                    onChange={(event) => updateTaskDraft({ labels: event.target.value })}
                    rows={4}
                  />
                </label>
                <label>
                  Acceptance Criteria
                  <textarea
                    value={taskModal.draft.acceptanceCriteria}
                    onChange={(event) => updateTaskDraft({ acceptanceCriteria: event.target.value })}
                    rows={4}
                  />
                </label>
              </div>
              <div className="modal-actions">
                <button type="button" onClick={() => setTaskModal(null)}>
                  Cancel
                </button>
                <button className="primary" type="submit">
                  <Save size={16} />
                  Save
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {noteModal && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setNoteModal(null);
            }
          }}
        >
          <section className="modal compact-modal" role="dialog" aria-modal="true" aria-labelledby="note-dialog-title">
            <header className="modal-header">
              <div>
                <span className="eyebrow">
                  <FileText size={15} />
                  Note
                </span>
                <h2 id="note-dialog-title">{noteModal.mode === "create" ? "Add Note" : "Edit Note"}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close note dialog"
                onClick={() => setNoteModal(null)}
              >
                <X size={18} />
              </button>
            </header>

            <form className="modal-form" onSubmit={(event) => void saveNote(event)}>
              <div className="field-grid">
                <label>
                  Title
                  <input
                    value={noteModal.draft.title}
                    onChange={(event) => updateNoteDraft({ title: event.target.value })}
                    required
                  />
                </label>
                <label>
                  Kind
                  <select
                    value={noteModal.draft.kind}
                    onChange={(event) => updateNoteDraft({ kind: event.target.value as DocumentKind })}
                  >
                    {documentKinds.map((kind) => (
                      <option key={kind} value={kind}>
                        {kind}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="full-span">
                  Linked Task
                  <select
                    value={noteModal.draft.linkedIdeaId}
                    onChange={(event) => updateNoteDraft({ linkedIdeaId: event.target.value })}
                  >
                    <option value="">None</option>
                    {state.ideas.map((idea) => (
                      <option key={idea.id} value={idea.id}>
                        {idea.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="full-span">
                  Content
                  <textarea
                    value={noteModal.draft.content}
                    onChange={(event) => updateNoteDraft({ content: event.target.value })}
                    rows={12}
                  />
                </label>
              </div>
              <div className="modal-actions">
                <button type="button" onClick={() => setNoteModal(null)}>
                  Cancel
                </button>
                <button className="primary" type="submit">
                  <Save size={16} />
                  Save
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {isMcpGuideOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setIsMcpGuideOpen(false);
            }
          }}
        >
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mcp-guide-title"
          >
            <header className="modal-header">
              <div>
                <span className="eyebrow">
                  <Network size={15} />
                  VibePod network
                </span>
                <h2 id="mcp-guide-title">MCP Integration Guide</h2>
                <p>Connect running agent containers to vibepod-board over Streamable HTTP.</p>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close MCP integration guide"
                onClick={() => setIsMcpGuideOpen(false)}
              >
                <X size={18} />
              </button>
            </header>

            <div className="endpoint-grid">
              {endpointOptions.map((endpoint) => (
                <section className="endpoint" key={endpoint.url}>
                  <h3>{endpoint.label}</h3>
                  <code>{endpoint.url}</code>
                  <p>{endpoint.description}</p>
                </section>
              ))}
            </div>

            <div className="integration-list">
              {integrationExamples.map((example) => (
                <section className="integration-example" key={example.id}>
                  <div className="integration-head">
                    <div>
                      <h3>{example.name}</h3>
                      <p>{example.description}</p>
                    </div>
                    <a href={example.docsUrl} target="_blank" rel="noreferrer">
                      <ExternalLink size={16} />
                      Docs
                    </a>
                  </div>
                  {example.command && (
                    <>
                      <h4>Command</h4>
                      <pre>
                        <code>{example.command}</code>
                      </pre>
                    </>
                  )}
                  <h4>{example.configLabel}</h4>
                  <pre>
                    <code>{example.config}</code>
                  </pre>
                  <p className="verify">{example.verify}</p>
                </section>
              ))}
            </div>
          </section>
        </div>
      )}
    </main>
  );
};

const countCards = (columns: BoardColumns) =>
  boardColumns.reduce((total, column) => total + (columns[column]?.length ?? 0), 0);

createRoot(document.getElementById("root")!).render(<App />);
