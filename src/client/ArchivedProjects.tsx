import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Group,
  List,
  Modal,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Download,
  Eye,
  Trash2,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import type {
  BoardColumn,
  BoardColumns,
  Idea,
  PlanDocument,
  Project,
} from "../shared/types.js";
import { api } from "./api.js";
import {
  formatProjectCounts,
  isProjectKeyConfirmed,
  nonEmptyColumns,
  projectDeletionConsequences,
  sortArchivedProjects,
} from "./archivedProjects.js";
import { MarkdownText } from "./Markdown.js";
import { formatNavigationPath, type NavigationState } from "./navigation.js";
import { formatTaskId } from "./taskIdentity.js";

const columnLabels: Record<BoardColumn, string> = {
  ready: "Ready",
  planned: "Planned",
  in_progress: "In Progress",
  review: "Review",
  pr_ready: "PR ready",
  done: "Done",
};

const formatArchivedAt = (value: string | null | undefined) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "";

type ProjectActions = {
  onExport: (project: Project) => Promise<boolean>;
  onRestore: (project: Project) => Promise<void>;
  onDelete: (project: Project) => Promise<void>;
};

/**
 * The archived projects: a list with export, restore and delete, or one
 * archived project opened read-only with its tasks, board and notes.
 */
export const ArchivedProjectsView = ({
  projects,
  projectId,
  reloadKey,
  navigateTo,
  ...actions
}: {
  projects: Project[];
  projectId: string;
  reloadKey: number;
  navigateTo: (navigation: NavigationState) => void;
} & ProjectActions) => {
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [busyId, setBusyId] = useState("");
  const archived = sortArchivedProjects(projects);
  const opened = projectId
    ? archived.find((project) => project.id === projectId)
    : undefined;

  const restore = async (project: Project) => {
    setBusyId(project.id);
    try {
      await actions.onRestore(project);
    } finally {
      setBusyId("");
    }
  };

  const projectButtons = (project: Project) => (
    <>
      <Button
        type="button"
        variant="light"
        color="gray"
        leftSection={<Download size={16} />}
        onClick={() => void actions.onExport(project)}
      >
        Export
      </Button>
      <Button
        type="button"
        variant="light"
        leftSection={<ArchiveRestore size={16} />}
        loading={busyId === project.id}
        onClick={() => void restore(project)}
      >
        Restore
      </Button>
      <Button
        type="button"
        variant="light"
        color="red"
        leftSection={<Trash2 size={16} />}
        onClick={() => setDeleteTarget(project)}
      >
        Delete permanently
      </Button>
    </>
  );

  const backToList = () =>
    navigateTo({ activeView: "archivedProjects", selectedProjectId: "" });

  return (
    <>
      {projectId && !opened && (
        <Paper className="empty-state" withBorder radius="md" p="lg">
          <Stack gap="sm" align="flex-start">
            <Title order={3}>Archived project not found</Title>
            <Text c="dimmed">
              It may have been restored or deleted in the meantime.
            </Text>
            <Button
              type="button"
              leftSection={<ArrowLeft size={16} />}
              onClick={backToList}
            >
              Archived projects
            </Button>
          </Stack>
        </Paper>
      )}
      {opened && (
        <ArchivedProject
          project={opened}
          reloadKey={reloadKey}
          onBack={backToList}
          buttons={projectButtons(opened)}
        />
      )}
      {!projectId && archived.length === 0 && (
        <Paper className="empty-state" withBorder radius="md" p="lg">
          <Stack gap="sm" align="flex-start">
            <Title order={3}>No archived projects</Title>
            <Text c="dimmed">
              Archive a finished or abandoned project from its project card to
              take it out of the project list. It shows up here, where it can be
              exported, restored or deleted.
            </Text>
          </Stack>
        </Paper>
      )}
      {!projectId && archived.length > 0 && (
        <SimpleGrid cols={{ base: 1, sm: 2, xl: 3 }} spacing="lg">
          {archived.map((project) => (
            <Card
              key={project.id}
              className="project-card"
              withBorder
              shadow="sm"
              padding="xl"
              radius="md"
            >
              <Stack h="100%" gap="md">
                <Box>
                  <Group gap="xs" mb="xs">
                    <Badge variant="light" color="gray">
                      {project.key}
                    </Badge>
                    <Badge variant="light" color="orange">
                      Archived
                    </Badge>
                  </Group>
                  <Title order={3}>
                    <Anchor
                      href={formatNavigationPath({
                        activeView: "archivedProjects",
                        selectedProjectId: project.id,
                      })}
                      inherit
                      c="inherit"
                      onClick={(event) => {
                        event.preventDefault();
                        navigateTo({
                          activeView: "archivedProjects",
                          selectedProjectId: project.id,
                        });
                      }}
                    >
                      {project.title}
                    </Anchor>
                  </Title>
                  <Text c="dimmed" size="sm" mt={6}>
                    Archived {formatArchivedAt(project.archivedAt)}
                  </Text>
                  <Text c="dimmed" size="sm">
                    {formatProjectCounts(project)}
                  </Text>
                </Box>
                <Group mt="auto" justify="flex-end" gap="xs">
                  <Button
                    type="button"
                    variant="light"
                    color="gray"
                    leftSection={<Eye size={16} />}
                    onClick={() =>
                      navigateTo({
                        activeView: "archivedProjects",
                        selectedProjectId: project.id,
                      })
                    }
                  >
                    Open
                  </Button>
                  {projectButtons(project)}
                </Group>
              </Stack>
            </Card>
          ))}
        </SimpleGrid>
      )}
      <DeleteProjectDialog
        project={deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        onExport={actions.onExport}
        onConfirm={async (project) => {
          await actions.onDelete(project);
          setDeleteTarget(null);
          if (projectId) backToList();
        }}
      />
    </>
  );
};

type ArchivedProjectData = {
  ideas: Idea[];
  columns: BoardColumns | null;
  documents: PlanDocument[];
};

/** One archived project, read-only: its tasks, board and notes. */
const ArchivedProject = ({
  project,
  reloadKey,
  onBack,
  buttons,
}: {
  project: Project;
  reloadKey: number;
  onBack: () => void;
  buttons: ReactNode;
}) => {
  const [data, setData] = useState<ArchivedProjectData | null>(null);
  const [error, setError] = useState("");

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey asks for a refresh
  useEffect(() => {
    let cancelled = false;
    const query = `projectId=${encodeURIComponent(project.id)}`;
    setError("");
    Promise.all([
      api<{ items: Idea[] }>(`/api/ideas?${query}`),
      api<{ columns: BoardColumns }>(`/api/board?${query}`),
      api<{ items: PlanDocument[] }>(`/api/documents?${query}`),
    ])
      .then(([ideas, board, documents]) => {
        if (cancelled) return;
        setData({
          ideas: [...ideas.items].sort((a, b) => a.taskNumber - b.taskNumber),
          columns: board.columns,
          documents: documents.items,
        });
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, reloadKey]);

  const taskKey = (idea: Idea) => formatTaskId(project.key, idea.taskNumber);
  const ideaById = new Map((data?.ideas ?? []).map((idea) => [idea.id, idea]));

  return (
    <Stack gap="md">
      <Paper withBorder radius="md" p="md">
        <Group justify="space-between" align="flex-start" gap="md">
          <Box>
            <Button
              type="button"
              variant="subtle"
              color="gray"
              size="compact-sm"
              leftSection={<ArrowLeft size={14} />}
              onClick={onBack}
              mb="xs"
            >
              Archived projects
            </Button>
            <Group gap="xs">
              <Badge variant="light" color="gray">
                {project.key}
              </Badge>
              <Title order={3}>{project.title}</Title>
            </Group>
            <Text c="dimmed" size="sm" mt={4}>
              Archived {formatArchivedAt(project.archivedAt)} ·{" "}
              {formatProjectCounts(project)} · read-only
            </Text>
            {project.summary && (
              <Text size="sm" mt="xs">
                {project.summary}
              </Text>
            )}
          </Box>
          <Group gap="xs">{buttons}</Group>
        </Group>
      </Paper>
      {error && (
        <Alert color="red" variant="light">
          {error}
        </Alert>
      )}
      {!data && !error && (
        <Alert color="blue" variant="light">
          Loading project...
        </Alert>
      )}
      {data && (
        <Tabs defaultValue="tasks" keepMounted={false}>
          <Tabs.List>
            <Tabs.Tab value="tasks">Tasks ({data.ideas.length})</Tabs.Tab>
            <Tabs.Tab value="board">Board</Tabs.Tab>
            <Tabs.Tab value="notes">Notes ({data.documents.length})</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="tasks" pt="md">
            {data.ideas.length === 0 ? (
              <Text c="dimmed">No tasks.</Text>
            ) : (
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Task</Table.Th>
                    <Table.Th>Title</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Labels</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.ideas.map((idea) => (
                    <Table.Tr key={idea.id}>
                      <Table.Td>
                        <Text size="sm" ff="monospace">
                          {taskKey(idea)}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" fw={600}>
                          {idea.title}
                        </Text>
                        {idea.summary && (
                          <Text size="xs" c="dimmed" lineClamp={2}>
                            {idea.summary}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Badge variant="light" size="sm">
                          {idea.status}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          {idea.labels.map((label) => (
                            <Badge
                              key={label}
                              variant="outline"
                              color="gray"
                              size="xs"
                            >
                              {label}
                            </Badge>
                          ))}
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Tabs.Panel>
          <Tabs.Panel value="board" pt="md">
            {!data.columns || nonEmptyColumns(data.columns).length === 0 ? (
              <Text c="dimmed">No cards on the board.</Text>
            ) : (
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
                {nonEmptyColumns(data.columns).map(({ column, cards }) => (
                  <Paper key={column} withBorder radius="md" p="sm">
                    <Text fw={700} size="sm" mb="xs">
                      {columnLabels[column]} ({cards.length})
                    </Text>
                    <Stack gap={6}>
                      {cards.map((card) => {
                        const idea = card.ideaId
                          ? ideaById.get(card.ideaId)
                          : undefined;
                        return (
                          <Paper key={card.id} withBorder radius="sm" p="xs">
                            <Text size="sm">
                              {idea && (
                                <Text span ff="monospace" c="dimmed" mr={6}>
                                  {taskKey(idea)}
                                </Text>
                              )}
                              {card.title}
                            </Text>
                            {card.branchName && (
                              <Text size="xs" c="dimmed" ff="monospace">
                                {card.branchName}
                              </Text>
                            )}
                          </Paper>
                        );
                      })}
                    </Stack>
                  </Paper>
                ))}
              </SimpleGrid>
            )}
          </Tabs.Panel>
          <Tabs.Panel value="notes" pt="md">
            {data.documents.length === 0 ? (
              <Text c="dimmed">No notes.</Text>
            ) : (
              <Stack gap="sm">
                {data.documents.map((document) => (
                  <Paper key={document.id} withBorder radius="md" p="md">
                    <Group gap="xs" mb="xs">
                      <Title order={4}>{document.title}</Title>
                      <Badge variant="light" color="grape" size="sm">
                        {document.kind.replace("_", " ")}
                      </Badge>
                    </Group>
                    {document.content ? (
                      <MarkdownText>{document.content}</MarkdownText>
                    ) : (
                      <Text c="dimmed" size="sm">
                        Empty note.
                      </Text>
                    )}
                  </Paper>
                ))}
              </Stack>
            )}
          </Tabs.Panel>
        </Tabs>
      )}
    </Stack>
  );
};

/** Confirms archiving a project, saying what it means first. */
export const ArchiveProjectDialog = ({
  project,
  onCancel,
  onConfirm,
}: {
  project: Project | null;
  onCancel: () => void;
  onConfirm: (project: Project) => Promise<void>;
}) => {
  const [isArchiving, setIsArchiving] = useState(false);
  const [error, setError] = useState("");

  const cancel = () => {
    if (isArchiving) return;
    setError("");
    onCancel();
  };

  const confirm = async () => {
    if (!project) return;
    setIsArchiving(true);
    setError("");
    try {
      await onConfirm(project);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive");
    } finally {
      setIsArchiving(false);
    }
  };

  return (
    <Modal
      opened={!!project}
      onClose={cancel}
      title="Archive Project"
      centered
      closeOnClickOutside={!isArchiving}
      closeOnEscape={!isArchiving}
    >
      {project && (
        <Stack gap="md">
          <Text>
            Archive <strong>{project.key}</strong> · {project.title}?
          </Text>
          <List size="sm" spacing={4}>
            <List.Item>
              It leaves the project list and project pickers, and its tasks,
              cards and notes become read-only.
            </List.Item>
            <List.Item>
              Its automation is paused, so no worker can claim work in it.
            </List.Item>
            <List.Item>
              It stays under Archived projects, where it can be exported,
              restored or deleted.
            </List.Item>
          </List>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}
          <Group justify="flex-end" gap="sm">
            <Button
              type="button"
              variant="default"
              onClick={cancel}
              disabled={isArchiving}
            >
              Cancel
            </Button>
            <Button
              type="button"
              color="orange"
              leftSection={<Archive size={16} />}
              loading={isArchiving}
              onClick={() => void confirm()}
            >
              Archive Project
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
};

/** Deletes an archived project for good once its key is typed. */
export const DeleteProjectDialog = ({
  project,
  onCancel,
  onConfirm,
  onExport,
}: {
  project: Project | null;
  onCancel: () => void;
  onConfirm: (project: Project) => Promise<void>;
  onExport: (project: Project) => Promise<boolean>;
}) => {
  const [typed, setTyped] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [exported, setExported] = useState(false);
  const [error, setError] = useState("");
  const confirmed = !!project && isProjectKeyConfirmed(typed, project.key);

  // A new target starts over.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset per project
  useEffect(() => {
    setTyped("");
    setExported(false);
    setError("");
  }, [project?.id]);

  const cancel = () => {
    if (isDeleting) return;
    onCancel();
  };

  const exportFirst = async () => {
    if (!project) return;
    setIsExporting(true);
    try {
      setExported(await onExport(project));
    } finally {
      setIsExporting(false);
    }
  };

  const confirm = async () => {
    if (!project || !confirmed) return;
    setIsDeleting(true);
    setError("");
    try {
      await onConfirm(project);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete");
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <Modal
      opened={!!project}
      onClose={cancel}
      title="Delete Project Permanently"
      centered
      closeOnClickOutside={!isDeleting}
      closeOnEscape={!isDeleting}
    >
      {project && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void confirm();
          }}
        >
          <Stack gap="md">
            <Text>
              Delete <strong>{project.key}</strong> · {project.title} and
              everything in it?
            </Text>
            <List size="sm" spacing={4}>
              {projectDeletionConsequences(project).map((line) => (
                <List.Item key={line}>{line}</List.Item>
              ))}
            </List>
            <Group gap="sm">
              <Button
                type="button"
                variant="light"
                color="gray"
                leftSection={<Download size={16} />}
                loading={isExporting}
                onClick={() => void exportFirst()}
              >
                Export first
              </Button>
              {exported && (
                <Text size="sm" c="green">
                  Exported.
                </Text>
              )}
            </Group>
            <TextInput
              label={`Type ${project.key} to confirm`}
              value={typed}
              onChange={(event) => setTyped(event.currentTarget.value)}
              autoComplete="off"
              data-autofocus
            />
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <Group justify="flex-end" gap="sm">
              <Button
                type="button"
                variant="default"
                onClick={cancel}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                color="red"
                leftSection={<Trash2 size={16} />}
                loading={isDeleting}
                disabled={!confirmed}
              >
                Delete permanently
              </Button>
            </Group>
          </Stack>
        </form>
      )}
    </Modal>
  );
};
