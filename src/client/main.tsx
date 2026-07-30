import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  Code,
  createTheme,
  Group,
  MantineProvider,
  Modal,
  MultiSelect,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  ThemeIcon,
  Title,
  useMantineColorScheme,
} from "@mantine/core";
import "@mantine/core/styles.css";
import {
  Columns3,
  ExternalLink,
  FileText,
  FolderKanban,
  GitBranch,
  KeyRound,
  ListChecks,
  LogOut,
  Moon,
  Pencil,
  PlugZap,
  Plus,
  RefreshCcw,
  Save,
  Sun,
} from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  endpointOptions,
  integrationExamples,
  integrationExamplesForToken,
} from "../shared/integrationExamples.js";
import {
  type ApiTokenSummary,
  type AuthMeResponse,
  type BoardCard,
  type BoardColumn,
  type BoardColumns,
  boardColumns,
  type CreateApiTokenInput,
  type CreatedApiTokenResponse,
  type DocumentKind,
  documentKinds,
  type Idea,
  type IdeaStatus,
  ideaStatuses,
  type PlanDocument,
  type Project,
  type ReadinessEvent,
} from "../shared/types.js";
import vibepodIconUrl from "./assets/icon.png";
import {
  navigationForProjectSelection,
  projectSelectorOptions,
  shouldShowProjectSidebar,
} from "./layoutNavigation.js";
import {
  formatNavigationPath,
  type NavigationState,
  type NavigationView,
  parseNavigationPath,
} from "./navigation.js";
import { githubRemoteToHttpsUrl } from "./repositoryUtils.js";
import {
  isCardReadinessStale,
  isReadinessStale,
  readinessColor,
  taskListCardView,
} from "./taskCardUtils.js";
import {
  emptyTaskDraft,
  type TaskDraft,
  taskDraftToIdeaPayload,
  taskToDraft,
} from "./taskDraftUtils.js";
import { formatTaskId } from "./taskIdentity.js";
import { filterAndSortTasks, type TaskSortOption } from "./taskListUtils.js";
import { taskOverviewForIdea } from "./taskOverviewUtils.js";
import "./styles.css";

type AppState = {
  projects: Project[];
  ideas: Idea[];
  columns: BoardColumns;
  documents: PlanDocument[];
};

type ActiveView = NavigationView;

type AuthState =
  | { status: "checking" }
  | { status: "authenticated"; username: string }
  | { status: "unauthenticated" };

type LoginDraft = {
  username: string;
  password: string;
};

type ProjectDraft = {
  id?: string;
  key: string;
  title: string;
  summary: string;
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

type TaskViewModalState = {
  ideaId: string;
  card: BoardCard;
};

type ReadinessModalState = {
  ideaId: string;
  ideaTitle: string;
};

type NoteModalState = {
  mode: "create" | "edit";
  draft: NoteDraft;
};

type ProjectModalState = {
  mode: "create" | "edit";
  draft: ProjectDraft;
};

type CreatedTokenState = {
  name: string;
  token: string;
} | null;

const emptyColumns: BoardColumns = {
  ready: [],
  planned: [],
  in_progress: [],
  review: [],
  done: [],
};

const columnLabels: Record<BoardColumn, string> = {
  ready: "Ready",
  planned: "Planned",
  in_progress: "In Progress",
  review: "Review",
  done: "Done",
};

const emptyProjectDraft = (): ProjectDraft => ({
  key: "",
  title: "",
  summary: "",
});

const emptyNoteDraft = (): NoteDraft => ({
  title: "",
  kind: "notes",
  content: "",
  linkedIdeaId: "",
});

const emptyLoginDraft = (): LoginDraft => ({
  username: "",
  password: "",
});

const emptyTokenDraft = (): CreateApiTokenInput => ({
  name: "",
  projectIds: [],
});

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as {
      error?: string;
    };
    throw new Error(error.error ?? `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
};

const initialNavigation = parseNavigationPath(window.location.pathname);

const appTheme = createTheme({
  primaryColor: "teal",
  defaultRadius: "md",
  black: "#1f2937",
  white: "#ffffff",
  fontFamily:
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  headings: {
    fontFamily:
      'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    fontWeight: "700",
  },
  colors: {
    teal: [
      "#e7f8f4",
      "#c8eee5",
      "#9adfd1",
      "#69cfbc",
      "#43bba8",
      "#2f9e8f",
      "#247f73",
      "#1d665d",
      "#194f49",
      "#113b36",
    ],
    slate: [
      "#f8fafc",
      "#f1f5f9",
      "#e2e8f0",
      "#cbd5e1",
      "#94a3b8",
      "#64748b",
      "#475569",
      "#334155",
      "#1f2937",
      "#0f172a",
    ],
  },
});

const App = () => {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const [auth, setAuth] = useState<AuthState>({ status: "checking" });
  const [loginDraft, setLoginDraft] = useState<LoginDraft>(emptyLoginDraft());
  const [state, setState] = useState<AppState>({
    projects: [],
    ideas: [],
    columns: emptyColumns,
    documents: [],
  });
  const [activeView, setActiveView] = useState<ActiveView>(
    initialNavigation.activeView,
  );
  const [selectedProjectId, setSelectedProjectId] = useState<string>(
    initialNavigation.selectedProjectId,
  );
  const [projectModal, setProjectModal] = useState<ProjectModalState | null>(
    null,
  );
  const [taskModal, setTaskModal] = useState<TaskModalState | null>(null);
  const [taskViewModal, setTaskViewModal] = useState<TaskViewModalState | null>(
    null,
  );
  const [readinessModal, setReadinessModal] =
    useState<ReadinessModalState | null>(null);
  const [readinessEvents, setReadinessEvents] = useState<
    ReadinessEvent[] | null
  >(null);
  const [readinessError, setReadinessError] = useState("");
  const [noteModal, setNoteModal] = useState<NoteModalState | null>(null);
  const [taskSort, setTaskSort] = useState<TaskSortOption>("created_desc");
  const [taskStatusFilter, setTaskStatusFilter] = useState<IdeaStatus | "">("");
  const [taskLabelFilter, setTaskLabelFilter] = useState("");
  const [taskSearch, setTaskSearch] = useState("");
  const [draggingCardId, setDraggingCardId] = useState("");
  const [dragOverColumn, setDragOverColumn] = useState<BoardColumn | null>(
    null,
  );
  const [error, setError] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [isMcpGuideOpen, setIsMcpGuideOpen] = useState(false);
  const [isTokenManagerOpen, setIsTokenManagerOpen] = useState(false);
  const [tokens, setTokens] = useState<ApiTokenSummary[]>([]);
  const [createdToken, setCreatedToken] = useState<CreatedTokenState>(null);
  const [tokenDraft, setTokenDraft] = useState<CreateApiTokenInput>(
    emptyTokenDraft(),
  );

  const loadState = async () => {
    setError("");
    const [projects, ideas, board, documents] = await Promise.all([
      api<{ items: Project[] }>("/api/projects"),
      api<{ items: Idea[] }>("/api/ideas"),
      api<{ columns: BoardColumns }>("/api/board"),
      api<{ items: PlanDocument[] }>("/api/documents"),
    ]);
    setState({
      projects: projects.items,
      ideas: ideas.items,
      columns: board.columns,
      documents: documents.items,
    });
    setIsLoading(false);
  };

  const loginAdmin = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      const me = await api<AuthMeResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(loginDraft),
      });
      setAuth({
        status: "authenticated",
        username: me.username ?? loginDraft.username,
      });
      setLoginDraft(emptyLoginDraft());
      setIsLoading(true);
      await loadState();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to sign in");
    }
  };

  const logoutAdmin = async () => {
    await api<AuthMeResponse>("/api/auth/logout", { method: "POST" }).catch(
      () => undefined,
    );
    setAuth({ status: "unauthenticated" });
    setState({ projects: [], ideas: [], columns: emptyColumns, documents: [] });
    setSelectedProjectId("");
    setActiveView("projects");
    setIsTokenManagerOpen(false);
  };

  const loadTokens = async () => {
    const response = await api<{ items: ApiTokenSummary[] }>("/api/tokens");
    setTokens(response.items);
  };

  const openTokenManager = async () => {
    setError("");
    setCreatedToken(null);
    setTokenDraft(emptyTokenDraft());
    setIsTokenManagerOpen(true);
    try {
      await loadTokens();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tokens");
    }
  };

  const createToken = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      const created = await api<CreatedApiTokenResponse>("/api/tokens", {
        method: "POST",
        body: JSON.stringify(tokenDraft),
      });
      setCreatedToken({ name: created.item.name, token: created.token });
      setTokenDraft(emptyTokenDraft());
      await loadTokens();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create token");
    }
  };

  const revokeToken = async (tokenId: string) => {
    setError("");
    await api(`/api/tokens/${tokenId}/revoke`, { method: "POST" });
    await loadTokens();
  };

  // biome-ignore lint/correctness/useExhaustiveDependencies: bootstrap must run once on mount only
  useEffect(() => {
    const bootstrap = async () => {
      try {
        const me = await api<AuthMeResponse>("/api/auth/me");
        if (!me.authenticated) {
          setAuth({ status: "unauthenticated" });
          setIsLoading(false);
          return;
        }
        setAuth({ status: "authenticated", username: me.username ?? "admin" });
        await loadState();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load board");
        setAuth({ status: "unauthenticated" });
        setIsLoading(false);
      }
    };

    void bootstrap();
  }, []);

  useEffect(() => {
    const normalizedPath = formatNavigationPath({
      activeView: initialNavigation.activeView,
      selectedProjectId: initialNavigation.selectedProjectId,
    });
    if (window.location.pathname !== normalizedPath) {
      window.history.replaceState(null, "", normalizedPath);
    }

    const updateNavigationFromUrl = () => {
      const nextNavigation = parseNavigationPath(window.location.pathname);
      setActiveView(nextNavigation.activeView);
      setSelectedProjectId(nextNavigation.selectedProjectId);
    };

    window.addEventListener("popstate", updateNavigationFromUrl);
    return () =>
      window.removeEventListener("popstate", updateNavigationFromUrl);
  }, []);

  useEffect(() => {
    if (!readinessModal) {
      setReadinessEvents(null);
      setReadinessError("");
      return;
    }
    let cancelled = false;
    api<{ items: ReadinessEvent[] }>(
      `/api/ideas/${readinessModal.ideaId}/readiness`,
    )
      .then((response) => {
        if (!cancelled) setReadinessEvents(response.items);
      })
      .catch((requestError: Error) => {
        if (!cancelled) setReadinessError(requestError.message);
      });
    return () => {
      cancelled = true;
    };
  }, [readinessModal]);

  useEffect(() => {
    if (
      !isMcpGuideOpen &&
      !isTokenManagerOpen &&
      !projectModal &&
      !taskModal &&
      !taskViewModal &&
      !noteModal &&
      !readinessModal
    ) {
      return;
    }

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsMcpGuideOpen(false);
        setIsTokenManagerOpen(false);
        setProjectModal(null);
        setTaskModal(null);
        setTaskViewModal(null);
        setNoteModal(null);
        setReadinessModal(null);
      }
    };

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [
    isMcpGuideOpen,
    isTokenManagerOpen,
    projectModal,
    taskModal,
    taskViewModal,
    noteModal,
    readinessModal,
  ]);

  const selectedProject = state.projects.find(
    (project) => project.id === selectedProjectId,
  );
  const projectIdeas = selectedProject
    ? state.ideas.filter((idea) => idea.projectId === selectedProject.id)
    : [];
  const projectColumns = selectedProject
    ? filterColumnsByProject(state.columns, selectedProject.id)
    : emptyColumns;
  const projectDocuments = selectedProject
    ? state.documents.filter(
        (document) => document.projectId === selectedProject.id,
      )
    : [];
  const taskViewIdea = taskViewModal
    ? (state.ideas.find((idea) => idea.id === taskViewModal.ideaId) ?? null)
    : null;
  const taskViewProject = taskViewIdea
    ? (state.projects.find(
        (project) => project.id === taskViewIdea.projectId,
      ) ?? null)
    : null;
  const taskViewCard = taskViewModal?.card ?? null;
  const ideaById = new Map(state.ideas.map((idea) => [idea.id, idea]));
  const taskOverview =
    taskViewIdea && taskViewProject
      ? taskOverviewForIdea(taskViewIdea, taskViewProject.key)
      : null;
  const taskLabelOptions = Array.from(
    new Set(projectIdeas.flatMap((idea) => idea.labels)),
  ).sort((a, b) => a.localeCompare(b));
  const taskModalLabelOptions = Array.from(
    new Set([...taskLabelOptions, ...(taskModal?.draft.labels ?? [])]),
  ).sort((a, b) => a.localeCompare(b));
  const visibleProjectIdeas = filterAndSortTasks(projectIdeas, {
    sort: taskSort,
    status: taskStatusFilter,
    label: taskLabelFilter,
    search: taskSearch,
  });
  const showProjectSidebar = shouldShowProjectSidebar(
    activeView,
    !!selectedProject,
  );
  const projectOptions = projectSelectorOptions(state.projects);
  const isDarkTheme = colorScheme === "dark";

  if (auth.status === "checking") {
    return (
      <main className="login-shell">
        <Paper className="login-panel" withBorder radius="md" p="lg">
          <Alert color="blue" variant="light">
            Loading board...
          </Alert>
        </Paper>
      </main>
    );
  }

  if (auth.status === "unauthenticated") {
    return (
      <main className="login-shell">
        <Paper className="login-panel" withBorder radius="md" p="xl">
          <form
            className="modal-form"
            onSubmit={(event) => void loginAdmin(event)}
          >
            <Stack gap="md">
              <Group gap="sm">
                <ThemeIcon variant="light" color="teal" size={42} radius="md">
                  <KeyRound size={22} />
                </ThemeIcon>
                <Box>
                  <Title order={2}>Admin Login</Title>
                  <Text c="dimmed">vibepod-board</Text>
                </Box>
              </Group>
              {error && (
                <Alert color="red" variant="light">
                  {error}
                </Alert>
              )}
              <TextInput
                label="Username"
                aria-label="Username"
                value={loginDraft.username}
                onChange={(event) =>
                  setLoginDraft((current) => ({
                    ...current,
                    username: event.target.value,
                  }))
                }
                required
              />
              <TextInput
                label="Password"
                aria-label="Password"
                type="password"
                value={loginDraft.password}
                onChange={(event) =>
                  setLoginDraft((current) => ({
                    ...current,
                    password: event.target.value,
                  }))
                }
                required
              />
              <Button type="submit" leftSection={<KeyRound size={16} />}>
                Sign In
              </Button>
            </Stack>
          </form>
        </Paper>
      </main>
    );
  }

  const navigateTo = (navigation: NavigationState) => {
    const path = formatNavigationPath(navigation);
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
    setSelectedProjectId(navigation.selectedProjectId);
    setActiveView(navigation.activeView);
  };

  const selectProject = (projectId: string | null) => {
    if (!projectId) {
      return;
    }
    navigateTo(navigationForProjectSelection(activeView, projectId));
  };

  const openProject = (
    project: Project,
    view: Exclude<ActiveView, "projects"> = "ideas",
  ) => {
    navigateTo({ activeView: view, selectedProjectId: project.id });
  };

  const returnToProjects = () => {
    navigateTo({ activeView: "projects", selectedProjectId: "" });
  };

  const openCreateProject = () => {
    setProjectModal({ mode: "create", draft: emptyProjectDraft() });
  };

  const openEditProject = (project: Project) => {
    setProjectModal({
      mode: "edit",
      draft: {
        id: project.id,
        key: project.key,
        title: project.title,
        summary: project.summary,
      },
    });
  };

  const updateProjectDraft = (patch: Partial<ProjectDraft>) => {
    setProjectModal((current) =>
      current ? { ...current, draft: { ...current.draft, ...patch } } : current,
    );
  };

  const saveProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectModal) {
      return;
    }
    setError("");
    const payload = {
      key: projectModal.draft.key,
      title: projectModal.draft.title,
      summary: projectModal.draft.summary,
    };

    try {
      if (projectModal.mode === "create") {
        await api<{ item: Project }>("/api/projects", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      } else if (projectModal.draft.id) {
        await api(`/api/projects/${projectModal.draft.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      }
      setProjectModal(null);
      await loadState();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save project");
    }
  };

  const openCreateTask = () => {
    if (!selectedProject) {
      returnToProjects();
      return;
    }
    setTaskModal({ mode: "create", draft: emptyTaskDraft() });
  };

  const openEditTask = (idea: Idea) => {
    const project = state.projects.find((item) => item.id === idea.projectId);
    setTaskModal({
      mode: "edit",
      draft: taskToDraft(idea, project?.key),
    });
  };

  const updateTaskDraft = (patch: Partial<TaskDraft>) => {
    setTaskModal((current) =>
      current ? { ...current, draft: { ...current.draft, ...patch } } : current,
    );
  };

  const saveTask = async (event: FormEvent) => {
    event.preventDefault();
    if (!taskModal) {
      return;
    }
    setError("");
    const payload = taskDraftToIdeaPayload(taskModal.draft);

    if (taskModal.mode === "create") {
      const created = await api<{ item: Idea }>("/api/ideas", {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          projectId: selectedProject?.id,
        }),
      });
      if (taskModal.draft.status !== "idea") {
        await api(`/api/ideas/${created.item.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: taskModal.draft.status }),
        });
      }
    } else if (taskModal.draft.id) {
      await api(`/api/ideas/${taskModal.draft.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...payload,
          status: taskModal.draft.status,
        }),
      });
    }
    setTaskModal(null);
    await loadState();
  };

  const openCreateNote = () => {
    if (!selectedProject) {
      returnToProjects();
      return;
    }
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
        linkedIdeaId: document.linkedIdeaIds[0] ?? "",
      },
    });
  };

  const updateNoteDraft = (patch: Partial<NoteDraft>) => {
    setNoteModal((current) =>
      current ? { ...current, draft: { ...current.draft, ...patch } } : current,
    );
  };

  const setBoardAvailability = async (idea: Idea, available: boolean) => {
    setError("");
    await api(`/api/ideas/${idea.id}/ready`, {
      method: "POST",
      body: JSON.stringify({ available }),
    });
    await loadState();
  };

  const openCardTask = (card: BoardCard) => {
    const idea = state.ideas.find((item) => item.id === card.ideaId);
    if (!idea) {
      setError("Task details are not available for this board card.");
      return;
    }
    setTaskViewModal({ ideaId: idea.id, card });
  };

  const moveCard = async (card: BoardCard, column: BoardColumn) => {
    setError("");
    await api(`/api/board/${card.id}`, {
      method: "PATCH",
      body: JSON.stringify({ column }),
    });
    await loadState();
  };

  const dropCard = async (column: BoardColumn) => {
    const card = boardColumns
      .flatMap((boardColumn) => projectColumns[boardColumn] ?? [])
      .find((item) => item.id === draggingCardId);
    setDraggingCardId("");
    setDragOverColumn(null);
    if (!card || card.column === column) {
      return;
    }
    await moveCard(card, column);
  };

  const saveNote = async (event: FormEvent) => {
    event.preventDefault();
    if (!noteModal) {
      return;
    }
    setError("");
    const payload = {
      projectId: selectedProject?.id,
      title: noteModal.draft.title,
      content: noteModal.draft.content,
      kind: noteModal.draft.kind,
      linkedIdeaIds: noteModal.draft.linkedIdeaId
        ? [noteModal.draft.linkedIdeaId]
        : [],
    };

    if (noteModal.mode === "create") {
      await api("/api/documents", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } else if (noteModal.draft.id) {
      await api(`/api/documents/${noteModal.draft.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    }
    setNoteModal(null);
    await loadState();
  };

  return (
    <main
      className={`shell ${activeView === "projects" ? "home-shell" : "project-shell"}`}
    >
      <header className="app-topbar">
        <a
          className="app-brand"
          href={formatNavigationPath({
            activeView: "projects",
            selectedProjectId: "",
          })}
          onClick={(event) => {
            event.preventDefault();
            returnToProjects();
          }}
        >
          <span className="app-brand-mark">
            <img src={vibepodIconUrl} alt="" />
          </span>
          <span>vibepod-board</span>
        </a>
        <Select
          className="project-jump"
          aria-label="Project"
          placeholder={
            state.projects.length > 0 ? "Select project" : "No projects"
          }
          value={selectedProject?.id ?? null}
          onChange={selectProject}
          data={projectOptions}
          searchable
          nothingFoundMessage="No projects"
          leftSection={<FolderKanban size={16} />}
        />
        <Group className="app-topbar-actions" gap="xs">
          <Switch
            className="theme-switch"
            aria-label="Toggle dark theme"
            checked={isDarkTheme}
            onChange={() => toggleColorScheme()}
            color="teal"
            size="md"
            onLabel={<Moon size={14} />}
            offLabel={<Sun size={14} />}
          />
          <Button
            type="button"
            variant="light"
            color="gray"
            leftSection={<PlugZap size={16} />}
            onClick={() => setIsMcpGuideOpen(true)}
          >
            MCP Integration
          </Button>
          <Button
            type="button"
            variant="light"
            color="gray"
            leftSection={<KeyRound size={16} />}
            onClick={() => void openTokenManager()}
          >
            API Tokens
          </Button>
          <Button
            type="button"
            variant="subtle"
            color="gray"
            leftSection={<LogOut size={16} />}
            onClick={() => void logoutAdmin()}
          >
            Logout
          </Button>
        </Group>
      </header>

      {showProjectSidebar && (
        <aside className="sidebar">
          <nav className="nav">
            <a
              className={activeView === "ideas" ? "active" : ""}
              href={
                selectedProject
                  ? formatNavigationPath({
                      activeView: "ideas",
                      selectedProjectId: selectedProject.id,
                    })
                  : formatNavigationPath({
                      activeView: "projects",
                      selectedProjectId: "",
                    })
              }
              aria-disabled={!selectedProject}
              onClick={(event) => {
                event.preventDefault();
                if (selectedProject) {
                  navigateTo({
                    activeView: "ideas",
                    selectedProjectId: selectedProject.id,
                  });
                }
              }}
            >
              <ListChecks size={18} />
              Tasks
            </a>
            <a
              className={activeView === "board" ? "active" : ""}
              href={
                selectedProject
                  ? formatNavigationPath({
                      activeView: "board",
                      selectedProjectId: selectedProject.id,
                    })
                  : formatNavigationPath({
                      activeView: "projects",
                      selectedProjectId: "",
                    })
              }
              aria-disabled={!selectedProject}
              onClick={(event) => {
                event.preventDefault();
                if (selectedProject) {
                  navigateTo({
                    activeView: "board",
                    selectedProjectId: selectedProject.id,
                  });
                }
              }}
            >
              <Columns3 size={18} />
              Board
            </a>
            <a
              className={activeView === "documents" ? "active" : ""}
              href={
                selectedProject
                  ? formatNavigationPath({
                      activeView: "documents",
                      selectedProjectId: selectedProject.id,
                    })
                  : formatNavigationPath({
                      activeView: "projects",
                      selectedProjectId: "",
                    })
              }
              aria-disabled={!selectedProject}
              onClick={(event) => {
                event.preventDefault();
                if (selectedProject) {
                  navigateTo({
                    activeView: "documents",
                    selectedProjectId: selectedProject.id,
                  });
                }
              }}
            >
              <FileText size={18} />
              Notes
            </a>
          </nav>
          {selectedProject && (
            <Paper className="project-context" withBorder radius="md" p="sm">
              <Text size="xs" fw={700} tt="uppercase">
                Current Project
              </Text>
              <Text fw={700}>{selectedProject.title}</Text>
            </Paper>
          )}
        </aside>
      )}

      <section className="workspace">
        <header className="topbar">
          <div>
            <Title order={2}>{viewTitle(activeView, selectedProject)}</Title>
            <Text c="dimmed">
              {activeView === "projects"
                ? `${state.projects.length} projects`
                : `${projectIdeas.length} tasks, ${countCards(projectColumns)} cards, ${projectDocuments.length} notes`}
            </Text>
          </div>
          <Group className="topbar-actions" gap="xs">
            {activeView === "projects" && (
              <Button
                type="button"
                leftSection={<Plus size={18} />}
                onClick={openCreateProject}
              >
                Add Project
              </Button>
            )}
            {activeView === "ideas" && selectedProject && (
              <Button
                type="button"
                leftSection={<Plus size={18} />}
                onClick={openCreateTask}
              >
                Add Task
              </Button>
            )}
            {activeView === "documents" && selectedProject && (
              <Button
                type="button"
                leftSection={<Plus size={18} />}
                onClick={openCreateNote}
              >
                Add Note
              </Button>
            )}
            <ActionIcon
              variant="light"
              color="gray"
              size={40}
              aria-label="Refresh"
              onClick={() => loadState()}
            >
              <RefreshCcw size={18} />
            </ActionIcon>
          </Group>
        </header>

        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        {isLoading ? (
          <Alert color="blue" variant="light">
            Loading board...
          </Alert>
        ) : null}

        {activeView === "projects" && (
          <>
            {state.projects.length === 0 && (
              <Paper className="empty-state" withBorder radius="md" p="lg">
                <Stack gap="sm" align="flex-start">
                  <Title order={3}>No projects yet</Title>
                  <Text c="dimmed">
                    Create a project to start adding tasks, board cards, and
                    notes.
                  </Text>
                  <Button
                    type="button"
                    leftSection={<Plus size={18} />}
                    onClick={openCreateProject}
                  >
                    Add Project
                  </Button>
                </Stack>
              </Paper>
            )}
            <SimpleGrid cols={{ base: 1, sm: 2, xl: 3 }} spacing="lg">
              {state.projects.map((project) => {
                const counts = projectCounts(project.id, state);
                return (
                  <Card
                    className="project-card"
                    key={project.id}
                    withBorder
                    shadow="sm"
                    padding="xl"
                    radius="md"
                    role="button"
                    tabIndex={0}
                    onClick={() => openProject(project, "ideas")}
                    onKeyDown={(event) => {
                      if (
                        event.target === event.currentTarget &&
                        (event.key === "Enter" || event.key === " ")
                      ) {
                        event.preventDefault();
                        openProject(project, "ideas");
                      }
                    }}
                  >
                    <Stack h="100%" gap="md">
                      <Group
                        align="flex-start"
                        justify="space-between"
                        gap="md"
                      >
                        <Box>
                          <Badge variant="light" color="gray" mb="xs">
                            {project.key}
                          </Badge>
                          <Title order={3}>{project.title}</Title>
                          <Text c="dimmed" mt={6} lineClamp={3}>
                            {project.summary || "No summary yet."}
                          </Text>
                        </Box>
                        <ThemeIcon
                          variant="light"
                          color="blue"
                          size={42}
                          radius="md"
                        >
                          <FolderKanban size={22} />
                        </ThemeIcon>
                      </Group>
                      <Group gap="xs">
                        <Badge variant="light" color="green">
                          {counts.tasks} tasks
                        </Badge>
                        <Badge variant="light" color="blue">
                          {counts.cards} cards
                        </Badge>
                        <Badge variant="light" color="grape">
                          {counts.notes} notes
                        </Badge>
                      </Group>
                      <Group mt="auto" justify="flex-end">
                        <Button
                          type="button"
                          variant="light"
                          color="gray"
                          leftSection={<Pencil size={16} />}
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            openEditProject(project);
                          }}
                        >
                          Edit
                        </Button>
                        <Button
                          type="button"
                          component="a"
                          href={formatNavigationPath({
                            activeView: "board",
                            selectedProjectId: project.id,
                          })}
                          variant="light"
                          leftSection={<Columns3 size={16} />}
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            openProject(project, "board");
                          }}
                        >
                          Board
                        </Button>
                      </Group>
                    </Stack>
                  </Card>
                );
              })}
            </SimpleGrid>
          </>
        )}

        {activeView !== "projects" && !selectedProject && (
          <Paper className="empty-state" withBorder radius="md" p="lg">
            <Stack gap="sm" align="flex-start">
              <Title order={3}>Select a project</Title>
              <Text c="dimmed">
                Tasks, board cards, and notes live inside a project.
              </Text>
              <Button
                type="button"
                leftSection={<FolderKanban size={18} />}
                onClick={returnToProjects}
              >
                View Projects
              </Button>
            </Stack>
          </Paper>
        )}

        {activeView === "ideas" && selectedProject && (
          <Stack gap="sm">
            <Paper className="task-filters" withBorder radius="md" p="md">
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
                <TextInput
                  label="Search"
                  placeholder="Search title, details, labels"
                  value={taskSearch}
                  onChange={(event) => setTaskSearch(event.currentTarget.value)}
                />
                <Select
                  label="Sort"
                  value={taskSort}
                  onChange={(value) =>
                    setTaskSort((value ?? "created_desc") as TaskSortOption)
                  }
                  data={[
                    { value: "created_desc", label: "Latest added" },
                    { value: "updated_desc", label: "Recently updated" },
                    { value: "title_asc", label: "Title A-Z" },
                    { value: "status_asc", label: "Status" },
                    { value: "rating_desc", label: "Rating" },
                  ]}
                />
                <Select
                  label="Status"
                  value={taskStatusFilter}
                  onChange={(value) =>
                    setTaskStatusFilter((value ?? "") as IdeaStatus | "")
                  }
                  data={[
                    { value: "", label: "All statuses" },
                    ...ideaStatuses.map((status) => ({
                      value: status,
                      label: status,
                    })),
                  ]}
                />
                <Select
                  label="Label"
                  value={taskLabelFilter}
                  onChange={(value) => setTaskLabelFilter(value ?? "")}
                  data={[
                    { value: "", label: "All labels" },
                    ...taskLabelOptions.map((label) => ({
                      value: label,
                      label,
                    })),
                  ]}
                />
              </SimpleGrid>
            </Paper>
            {projectIdeas.length === 0 && (
              <Paper
                className="empty-state inline"
                withBorder
                radius="md"
                p="md"
              >
                <Text c="dimmed">No tasks in this project yet.</Text>
              </Paper>
            )}
            {projectIdeas.length > 0 && visibleProjectIdeas.length === 0 && (
              <Paper
                className="empty-state inline"
                withBorder
                radius="md"
                p="md"
              >
                <Text c="dimmed">No tasks match the current filters.</Text>
              </Paper>
            )}
            {visibleProjectIdeas.map((idea) => {
              const taskCard = taskListCardView(
                idea,
                state.columns,
                selectedProject.key,
              );
              return (
                <Card
                  className="item task-list-card"
                  withBorder
                  shadow="xs"
                  radius="md"
                  padding={0}
                  key={taskCard.id}
                >
                  <Box
                    className="task-card-edit-area"
                    role="button"
                    tabIndex={0}
                    onClick={() => openEditTask(idea)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        openEditTask(idea);
                      }
                    }}
                  >
                    <Group align="flex-start" justify="space-between" gap="sm">
                      <Box className="task-card-title">
                        <Group
                          className="task-card-title-row"
                          gap="xs"
                          wrap="nowrap"
                        >
                          <Badge variant="light" color="gray" size="sm">
                            {taskCard.taskId}
                          </Badge>
                          <Title order={3}>{taskCard.title}</Title>
                        </Group>
                        {labelBadges(taskCard.labels)}
                        {idea.readinessScore !== undefined && (
                          <Badge
                            variant="light"
                            color={
                              isReadinessStale(idea)
                                ? "gray"
                                : readinessColor(idea.readinessScore)
                            }
                            size="sm"
                            opacity={isReadinessStale(idea) ? 0.6 : 1}
                            mt="xs"
                            style={{ cursor: "pointer" }}
                            onClick={(event) => {
                              event.stopPropagation();
                              setReadinessModal({
                                ideaId: idea.id,
                                ideaTitle: idea.title,
                              });
                            }}
                          >
                            {idea.readinessScore}/10
                            {isReadinessStale(idea) ? " · stale" : ""}
                          </Badge>
                        )}
                      </Box>
                      {statusBadge(taskCard.status)}
                    </Group>
                  </Box>
                  <Box
                    className="task-ready-control"
                    onClick={(event) => event.stopPropagation()}
                    onPointerDown={(event) => event.stopPropagation()}
                  >
                    <Checkbox
                      label="Ready"
                      checked={taskCard.isReady}
                      onChange={(event) =>
                        void setBoardAvailability(
                          idea,
                          event.currentTarget.checked,
                        )
                      }
                    />
                  </Box>
                </Card>
              );
            })}
          </Stack>
        )}

        {activeView === "board" && selectedProject && (
          <Stack className="board-shell" gap="sm">
            <Paper className="board-title" withBorder radius="md" p="md">
              <Group align="center" justify="space-between" gap="md">
                <Box>
                  <Title order={3}>{selectedProject.title}</Title>
                  <Text c="dimmed" size="sm">
                    Kanban board
                  </Text>
                </Box>
                <Badge variant="light" color="blue">
                  {countCards(projectColumns)} cards
                </Badge>
              </Group>
            </Paper>
            <div className="board">
              {boardColumns.map((column) => (
                <Paper
                  className={`column ${dragOverColumn === column ? "drop-active" : ""}`}
                  withBorder
                  radius="md"
                  p={0}
                  key={column}
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                    setDragOverColumn(column);
                  }}
                  onDragLeave={(event) => {
                    const relatedTarget = event.relatedTarget;
                    if (
                      relatedTarget instanceof Node &&
                      event.currentTarget.contains(relatedTarget)
                    ) {
                      return;
                    }
                    setDragOverColumn(null);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    void dropCard(column);
                  }}
                >
                  <Group className="column-head" justify="space-between" p="sm">
                    <Title className="column-title" order={4}>
                      {columnLabels[column]}
                    </Title>
                    <Badge variant="light" color="gray">
                      {projectColumns[column]?.length ?? 0}
                    </Badge>
                  </Group>
                  <Stack className="column-card-list" gap="xs" p="sm">
                    {(projectColumns[column] ?? []).map((card) => {
                      const linkedIdea = state.ideas.find(
                        (idea) => idea.id === card.ideaId,
                      );
                      const cardTaskId = linkedIdea
                        ? formatTaskId(
                            selectedProject.key,
                            linkedIdea.taskNumber,
                          )
                        : null;
                      const repositoryUrl = card.repositoryRemoteUrl
                        ? githubRemoteToHttpsUrl(card.repositoryRemoteUrl)
                        : undefined;
                      return (
                        <Card
                          className="compact-card"
                          key={card.id}
                          withBorder
                          shadow="xs"
                          padding="sm"
                          radius="md"
                          role="button"
                          tabIndex={0}
                          draggable
                          aria-grabbed={draggingCardId === card.id}
                          onClick={() => openCardTask(card)}
                          onDragStart={(event) => {
                            setDraggingCardId(card.id);
                            event.dataTransfer.effectAllowed = "move";
                            event.dataTransfer.setData("text/plain", card.id);
                          }}
                          onDragEnd={() => {
                            setDraggingCardId("");
                            setDragOverColumn(null);
                          }}
                          onKeyDown={(event) => {
                            if (
                              event.target === event.currentTarget &&
                              (event.key === "Enter" || event.key === " ")
                            ) {
                              event.preventDefault();
                              openCardTask(card);
                            }
                          }}
                        >
                          <Stack gap="xs">
                            <Title order={4}>{card.title}</Title>
                            {labelBadges(card.labels, "xs")}
                            {card.readinessScore !== undefined && (
                              <Group
                                className="board-card-readiness"
                                justify="flex-start"
                              >
                                <Badge
                                  variant="light"
                                  color={
                                    isCardReadinessStale(card, ideaById)
                                      ? "gray"
                                      : readinessColor(card.readinessScore)
                                  }
                                  size="sm"
                                  opacity={
                                    isCardReadinessStale(card, ideaById)
                                      ? 0.6
                                      : 1
                                  }
                                  style={{ cursor: "pointer" }}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    if (card.ideaId) {
                                      setReadinessModal({
                                        ideaId: card.ideaId,
                                        ideaTitle: card.title,
                                      });
                                    }
                                  }}
                                >
                                  {card.readinessScore}/10
                                  {isCardReadinessStale(card, ideaById)
                                    ? " · stale"
                                    : ""}
                                </Badge>
                              </Group>
                            )}
                            {card.branchName && (
                              <Group
                                className="board-card-branch"
                                justify="flex-start"
                              >
                                <Badge
                                  leftSection={
                                    <GitBranch size={12} aria-hidden />
                                  }
                                  variant="light"
                                  color="teal"
                                  size="sm"
                                >
                                  {card.branchName}
                                </Badge>
                              </Group>
                            )}
                            {(card.repositoryLocalPath ||
                              card.repositoryRemoteUrl) && (
                              <Stack className="board-card-repository" gap={4}>
                                {card.repositoryLocalPath && (
                                  <Code className="board-card-repository-path">
                                    {card.repositoryLocalPath}
                                  </Code>
                                )}
                                {card.repositoryRemoteUrl &&
                                  (repositoryUrl ? (
                                    <Anchor
                                      className="board-card-repository-link"
                                      href={repositoryUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                      onClick={(event) =>
                                        event.stopPropagation()
                                      }
                                    >
                                      <ExternalLink size={12} />
                                      GitHub
                                    </Anchor>
                                  ) : (
                                    <Text
                                      className="board-card-repository-remote"
                                      size="xs"
                                    >
                                      {card.repositoryRemoteUrl}
                                    </Text>
                                  ))}
                              </Stack>
                            )}
                            {cardTaskId && (
                              <Group
                                className="board-card-task-id"
                                justify="flex-end"
                              >
                                <Badge variant="light" color="gray" size="sm">
                                  {cardTaskId}
                                </Badge>
                              </Group>
                            )}
                          </Stack>
                        </Card>
                      );
                    })}
                  </Stack>
                </Paper>
              ))}
            </div>
          </Stack>
        )}

        {activeView === "documents" && selectedProject && (
          <Stack gap="sm">
            {projectDocuments.length === 0 && (
              <Paper
                className="empty-state inline"
                withBorder
                radius="md"
                p="md"
              >
                <Text c="dimmed">No notes in this project yet.</Text>
              </Paper>
            )}
            {projectDocuments.map((document) => (
              <Card
                className="item document"
                withBorder
                shadow="xs"
                radius="md"
                padding="md"
                key={document.id}
              >
                <Group align="flex-start" justify="space-between">
                  <Title order={3}>{document.title}</Title>
                  <Badge variant="light" color="violet">
                    {document.kind}
                  </Badge>
                </Group>
                <pre>{document.content || "No note content yet."}</pre>
                <Group gap="xs">
                  <Button
                    type="button"
                    variant="light"
                    color="gray"
                    leftSection={<Pencil size={16} />}
                    onClick={() => openEditNote(document)}
                  >
                    Edit
                  </Button>
                </Group>
              </Card>
            ))}
          </Stack>
        )}
      </section>

      <Modal
        opened={!!projectModal}
        onClose={() => setProjectModal(null)}
        title={projectModal?.mode === "create" ? "Add Project" : "Edit Project"}
        centered
        size="lg"
      >
        {projectModal && (
          <form
            className="modal-form"
            onSubmit={(event) => void saveProject(event)}
          >
            <Stack gap="md">
              <TextInput
                label="Project ID"
                value={projectModal.draft.key}
                onChange={(event) =>
                  updateProjectDraft({
                    key: normalizeProjectKeyInput(event.target.value),
                  })
                }
                description="1 to 3 capital letters"
                maxLength={3}
                required
              />
              <TextInput
                label="Title"
                value={projectModal.draft.title}
                onChange={(event) =>
                  updateProjectDraft({ title: event.target.value })
                }
                required
              />
              <Textarea
                label="Summary"
                value={projectModal.draft.summary}
                onChange={(event) =>
                  updateProjectDraft({ summary: event.target.value })
                }
                rows={4}
              />
              <Group justify="flex-end">
                <Button
                  type="button"
                  variant="default"
                  onClick={() => setProjectModal(null)}
                >
                  Cancel
                </Button>
                <Button type="submit" leftSection={<Save size={16} />}>
                  Save
                </Button>
              </Group>
            </Stack>
          </form>
        )}
      </Modal>

      <Modal
        opened={!!taskOverview}
        onClose={() => setTaskViewModal(null)}
        title="Task Overview"
        centered
        size="xl"
        classNames={{ body: "task-overview-modal-body" }}
      >
        {taskOverview && taskViewIdea && (
          <Stack gap="lg">
            <Stack gap="xs">
              <Badge variant="light" color="gray" w="fit-content">
                {taskOverview.taskId}
              </Badge>
              <Group align="flex-start" justify="space-between" gap="md">
                <Title order={2}>{taskOverview.title}</Title>
                {statusBadge(taskOverview.status)}
              </Group>
              {labelBadges(taskOverview.labels)}
            </Stack>

            <Paper className="overview-section" withBorder radius="md" p="md">
              <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                Description
              </Text>
              <Text className="overview-text">
                {taskViewCard?.details?.trim() || taskOverview.description}
              </Text>
            </Paper>

            <Paper className="overview-section" withBorder radius="md" p="md">
              <Group align="center" justify="space-between" mb="xs">
                <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                  Acceptance Criteria
                </Text>
                <Badge variant="light" color="gray">
                  {taskOverview.acceptanceCriteria.length}
                </Badge>
              </Group>
              {taskOverview.acceptanceCriteria.length > 0 ? (
                <ul className="overview-criteria-list">
                  {taskOverview.acceptanceCriteria.map((criterion) => (
                    <li key={criterion}>{criterion}</li>
                  ))}
                </ul>
              ) : (
                <Text c="dimmed">No acceptance criteria yet.</Text>
              )}
            </Paper>

            <Group justify="flex-end">
              <Button
                type="button"
                variant="default"
                onClick={() => setTaskViewModal(null)}
              >
                Close
              </Button>
              <Button
                type="button"
                variant="light"
                leftSection={<Pencil size={16} />}
                onClick={() => {
                  setTaskViewModal(null);
                  openEditTask(taskViewIdea);
                }}
              >
                Edit Task
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal
        opened={!!readinessModal}
        onClose={() => setReadinessModal(null)}
        title={
          readinessModal
            ? `Rating History — ${readinessModal.ideaTitle}`
            : "Rating History"
        }
        centered
        size="lg"
      >
        {readinessError && <Text c="red">{readinessError}</Text>}
        {!readinessError && readinessEvents === null && (
          <Text c="dimmed">Loading…</Text>
        )}
        {!readinessError &&
          readinessEvents !== null &&
          readinessEvents.length === 0 && (
            <Text c="dimmed">No ratings yet.</Text>
          )}
        {!readinessError &&
          readinessEvents !== null &&
          readinessEvents.length > 0 && (
            <Stack gap="sm">
              {readinessEvents.map((event, index) => {
                const latest = index === 0;
                const ratedIdea = readinessModal
                  ? ideaById.get(readinessModal.ideaId)
                  : undefined;
                const stale =
                  latest &&
                  ratedIdea !== undefined &&
                  isReadinessStale(ratedIdea);
                return (
                  <Paper
                    key={event.id}
                    withBorder
                    radius="md"
                    p="md"
                    style={
                      latest
                        ? { borderColor: "var(--mantine-color-blue-5)" }
                        : undefined
                    }
                  >
                    <Group align="center" justify="space-between" mb={4}>
                      <Badge
                        variant="light"
                        color={stale ? "gray" : readinessColor(event.score)}
                        opacity={stale ? 0.6 : 1}
                      >
                        {event.score}/10{stale ? " · stale" : ""}
                      </Badge>
                      <Text size="xs" c="dimmed">
                        {latest ? "Latest · " : ""}
                        {new Date(event.createdAt).toLocaleString()}
                      </Text>
                    </Group>
                    <Text className="overview-text">{event.reason}</Text>
                  </Paper>
                );
              })}
            </Stack>
          )}
      </Modal>

      <Modal
        opened={!!taskModal}
        onClose={() => setTaskModal(null)}
        title={taskModal?.mode === "create" ? "Add Task" : "Edit Task"}
        centered
        size="xl"
        classNames={{ header: "task-modal-header", body: "task-modal-body" }}
      >
        {taskModal && (
          <form
            className="modal-form"
            onSubmit={(event) => void saveTask(event)}
          >
            <Stack gap="md">
              {taskModal.draft.taskId && (
                <Badge variant="light" color="gray" w="fit-content">
                  {taskModal.draft.taskId}
                </Badge>
              )}
              <TextInput
                label="Title"
                value={taskModal.draft.title}
                onChange={(event) =>
                  updateTaskDraft({ title: event.target.value })
                }
                required
                size="lg"
              />
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <Select
                  label="Status"
                  value={taskModal.draft.status}
                  onChange={(value) =>
                    updateTaskDraft({ status: (value ?? "idea") as IdeaStatus })
                  }
                  data={ideaStatuses.map((status) => ({
                    value: status,
                    label: status,
                  }))}
                />
                <TagsInput
                  label="Labels"
                  value={taskModal.draft.labels}
                  onChange={(labels) => updateTaskDraft({ labels })}
                  data={taskModalLabelOptions}
                  placeholder="Select or type labels"
                  clearable
                  splitChars={[","]}
                />
              </SimpleGrid>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <TextInput
                  label="Repository Local Path"
                  value={taskModal.draft.repositoryLocalPath}
                  onChange={(event) =>
                    updateTaskDraft({ repositoryLocalPath: event.target.value })
                  }
                  placeholder="/workspace/vibepod-cli"
                />
                <TextInput
                  label="Repository Remote URL"
                  value={taskModal.draft.repositoryRemoteUrl}
                  onChange={(event) =>
                    updateTaskDraft({ repositoryRemoteUrl: event.target.value })
                  }
                  placeholder="git@github.com:owner/repo.git"
                />
              </SimpleGrid>
              <Textarea
                label="Description"
                value={taskModal.draft.description}
                onChange={(event) =>
                  updateTaskDraft({ description: event.target.value })
                }
                classNames={{ input: "task-description-input" }}
                rows={7}
              />
              <Textarea
                label="Acceptance Criteria"
                value={taskModal.draft.acceptanceCriteria}
                onChange={(event) =>
                  updateTaskDraft({ acceptanceCriteria: event.target.value })
                }
                classNames={{ input: "task-criteria-input" }}
                rows={7}
                autosize
                minRows={7}
              />
              <Group justify="flex-end">
                <Button
                  type="button"
                  variant="default"
                  onClick={() => setTaskModal(null)}
                >
                  Cancel
                </Button>
                <Button type="submit" leftSection={<Save size={16} />}>
                  Save
                </Button>
              </Group>
            </Stack>
          </form>
        )}
      </Modal>

      <Modal
        opened={!!noteModal}
        onClose={() => setNoteModal(null)}
        title={noteModal?.mode === "create" ? "Add Note" : "Edit Note"}
        centered
        size="lg"
      >
        {noteModal && (
          <form
            className="modal-form"
            onSubmit={(event) => void saveNote(event)}
          >
            <Stack gap="md">
              <TextInput
                label="Title"
                value={noteModal.draft.title}
                onChange={(event) =>
                  updateNoteDraft({ title: event.target.value })
                }
                required
              />
              <Select
                label="Kind"
                value={noteModal.draft.kind}
                onChange={(value) =>
                  updateNoteDraft({ kind: (value ?? "notes") as DocumentKind })
                }
                data={documentKinds.map((kind) => ({
                  value: kind,
                  label: kind,
                }))}
              />
              <Select
                label="Linked Task"
                value={noteModal.draft.linkedIdeaId}
                onChange={(value) =>
                  updateNoteDraft({ linkedIdeaId: value ?? "" })
                }
                data={[
                  { value: "", label: "None" },
                  ...projectIdeas.map((idea) => ({
                    value: idea.id,
                    label: idea.title,
                  })),
                ]}
              />
              <Textarea
                label="Content"
                value={noteModal.draft.content}
                onChange={(event) =>
                  updateNoteDraft({ content: event.target.value })
                }
                rows={12}
              />
              <Group justify="flex-end">
                <Button
                  type="button"
                  variant="default"
                  onClick={() => setNoteModal(null)}
                >
                  Cancel
                </Button>
                <Button type="submit" leftSection={<Save size={16} />}>
                  Save
                </Button>
              </Group>
            </Stack>
          </form>
        )}
      </Modal>

      <Modal
        opened={isTokenManagerOpen}
        onClose={() => setIsTokenManagerOpen(false)}
        title="API Tokens"
        centered
        size="xl"
      >
        <Stack gap="lg">
          {createdToken && (
            <Paper className="token-created" withBorder radius="md" p="md">
              <Stack gap="sm">
                <Group align="center" justify="space-between">
                  <Title order={3}>{createdToken.name}</Title>
                  <Badge variant="light" color="green">
                    New Token
                  </Badge>
                </Group>
                <Code block>{createdToken.token}</Code>
                <Stack className="token-config" gap="md">
                  {integrationExamplesForToken(createdToken.token).map(
                    (example) => (
                      <Paper
                        className="integration-example"
                        withBorder
                        radius="md"
                        p="md"
                        key={example.id}
                      >
                        <Stack gap="sm">
                          <Title order={4}>{example.name}</Title>
                          {example.command && (
                            <pre>
                              <code>{example.command}</code>
                            </pre>
                          )}
                          <Text size="sm" fw={700}>
                            {example.configLabel}
                          </Text>
                          <pre>
                            <code>{example.config}</code>
                          </pre>
                        </Stack>
                      </Paper>
                    ),
                  )}
                </Stack>
              </Stack>
            </Paper>
          )}

          <Paper withBorder radius="md" p="md">
            <form
              className="modal-form"
              onSubmit={(event) => void createToken(event)}
            >
              <Stack gap="md">
                <Title order={3}>Create Token</Title>
                <TextInput
                  label="Token Name"
                  value={tokenDraft.name}
                  onChange={(event) =>
                    setTokenDraft((current) => ({
                      ...current,
                      name: event.currentTarget.value,
                    }))
                  }
                  required
                />
                <MultiSelect
                  label="Projects"
                  value={tokenDraft.projectIds}
                  onChange={(projectIds) =>
                    setTokenDraft((current) => ({ ...current, projectIds }))
                  }
                  data={projectOptions}
                  searchable
                  required
                />
                <Group justify="flex-end">
                  <Button type="submit" leftSection={<KeyRound size={16} />}>
                    Create Token
                  </Button>
                </Group>
              </Stack>
            </form>
          </Paper>

          <Stack className="token-list" gap="sm">
            {tokens.length === 0 && (
              <Paper
                className="empty-state inline"
                withBorder
                radius="md"
                p="md"
              >
                <Text c="dimmed">No API tokens yet.</Text>
              </Paper>
            )}
            {tokens.map((token) => (
              <Paper withBorder radius="md" p="md" key={token.id}>
                <Group align="flex-start" justify="space-between" gap="md">
                  <Box>
                    <Group gap="xs">
                      <Title order={3}>{token.name}</Title>
                      {token.revokedAt && (
                        <Badge variant="light" color="red">
                          Revoked
                        </Badge>
                      )}
                    </Group>
                    <Group gap={6} mt="xs">
                      {token.projects.map((project) => (
                        <Badge key={project.id} variant="light" color="gray">
                          {project.key}
                        </Badge>
                      ))}
                    </Group>
                    <Text c="dimmed" size="sm" mt="xs">
                      Created {formatDateTime(token.createdAt)}
                      {token.lastUsedAt
                        ? `, last used ${formatDateTime(token.lastUsedAt)}`
                        : ""}
                    </Text>
                  </Box>
                  <Button
                    type="button"
                    variant="light"
                    color="red"
                    disabled={!!token.revokedAt}
                    onClick={() => void revokeToken(token.id)}
                  >
                    Revoke
                  </Button>
                </Group>
              </Paper>
            ))}
          </Stack>
        </Stack>
      </Modal>

      <Modal
        opened={isMcpGuideOpen}
        onClose={() => setIsMcpGuideOpen(false)}
        title="MCP Integration Guide"
        centered
        size="xl"
      >
        <Stack gap="lg">
          <Text c="dimmed">
            Connect running agent containers to vibepod-board over Streamable
            HTTP.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
            {endpointOptions.map((endpoint) => (
              <Paper
                className="endpoint"
                withBorder
                radius="md"
                p="md"
                key={endpoint.url}
              >
                <Stack gap="xs">
                  <Title order={3}>{endpoint.label}</Title>
                  <Code>{endpoint.url}</Code>
                  <Text c="dimmed">{endpoint.description}</Text>
                </Stack>
              </Paper>
            ))}
          </SimpleGrid>

          <Stack gap="md">
            {integrationExamples.map((example) => (
              <Paper
                className="integration-example"
                withBorder
                radius="md"
                p="md"
                key={example.id}
              >
                <Stack gap="sm">
                  <Group align="flex-start" justify="space-between">
                    <Box>
                      <Title order={3}>{example.name}</Title>
                      <Text c="dimmed">{example.description}</Text>
                    </Box>
                    <Anchor
                      href={example.docsUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Group gap={6}>
                        <ExternalLink size={16} />
                        Docs
                      </Group>
                    </Anchor>
                  </Group>
                  {example.command && (
                    <>
                      <Title order={4}>Command</Title>
                      <pre>
                        <code>{example.command}</code>
                      </pre>
                    </>
                  )}
                  <Title order={4}>{example.configLabel}</Title>
                  <pre>
                    <code>{example.config}</code>
                  </pre>
                  <Text className="verify" c="dimmed">
                    {example.verify}
                  </Text>
                </Stack>
              </Paper>
            ))}
          </Stack>
        </Stack>
      </Modal>
    </main>
  );
};

export const AppShell = () => (
  <MantineProvider defaultColorScheme="light" theme={appTheme}>
    <App />
  </MantineProvider>
);

const countCards = (columns: BoardColumns) =>
  boardColumns.reduce(
    (total, column) => total + (columns[column]?.length ?? 0),
    0,
  );

const filterColumnsByProject = (
  columns: BoardColumns,
  projectId: string,
): BoardColumns => {
  const filtered: BoardColumns = {
    ready: [],
    planned: [],
    in_progress: [],
    review: [],
    done: [],
  };
  for (const column of boardColumns) {
    filtered[column] = (columns[column] ?? []).filter(
      (card) => card.projectId === projectId,
    );
  }
  return filtered;
};

const projectCounts = (projectId: string, state: AppState) => {
  const columns = filterColumnsByProject(state.columns, projectId);
  return {
    tasks: state.ideas.filter((idea) => idea.projectId === projectId).length,
    cards: countCards(columns),
    notes: state.documents.filter(
      (document) => document.projectId === projectId,
    ).length,
  };
};

const normalizeProjectKeyInput = (value: string) =>
  value
    .toUpperCase()
    .replace(/[^A-Z]/g, "")
    .slice(0, 3);

const formatDateTime = (value: string) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "";

const statusColors: Record<IdeaStatus, string> = {
  idea: "gray",
  refining: "yellow",
  ready: "green",
  denied: "red",
};

const statusBadge = (status: IdeaStatus) => (
  <Badge variant="light" color={statusColors[status]} tt="capitalize">
    {status}
  </Badge>
);

const labelBadges = (labels: string[], size: "xs" | "sm" = "sm"): ReactNode =>
  labels.length > 0 ? (
    <Group gap={6}>
      {labels.map((label) => (
        <Badge
          className="label-badge"
          key={label}
          variant="outline"
          color="gray"
          size={size}
        >
          {label}
        </Badge>
      ))}
    </Group>
  ) : null;

const viewTitle = (activeView: ActiveView, project: Project | undefined) => {
  if (activeView === "projects") {
    return "Projects";
  }
  if (!project) {
    return "Select a Project";
  }
  if (activeView === "ideas") {
    return `${project.title} Tasks`;
  }
  if (activeView === "board") {
    return "Kanban Board";
  }
  return `${project.title} Notes`;
};

const rootElement = document.getElementById("root");
if (rootElement) {
  createRoot(rootElement).render(<AppShell />);
}
