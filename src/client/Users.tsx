import {
  Alert,
  Badge,
  Box,
  Button,
  Group,
  Modal,
  MultiSelect,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { KeyRound, Pencil, Trash2, UserPlus } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import type {
  Project,
  UserCredentialsResponse,
  UserSummary,
} from "../shared/types.js";
import { api } from "./api.js";
import { CopyableCode } from "./CopyableCode.js";
import { formatDateTime } from "./dateTime.js";

/** A temporary password the server just sent; it cannot be read again. */
type Credentials = {
  username: string;
  password: string;
  reason: "created" | "reset";
} | null;

/** An action that waits for the admin's yes. */
type Confirmation = { action: "reset" | "delete"; user: UserSummary } | null;

type ProjectEdit = { userId: string; projectIds: string[] } | null;

const fetchUsers = async () =>
  (await api<{ items: UserSummary[] }>("/api/users")).items;

/** The short name of a project on a badge; an archived one says so. */
export const projectBadge = (project: Project) =>
  project.archivedAt ? `${project.key} (archived)` : project.key;

/** When the user last signed in, in words. */
export const lastSignIn = (user: UserSummary) =>
  user.lastLoginAt
    ? `Last sign-in ${formatDateTime(user.lastLoginAt)}`
    : "Never signed in";

const projectChoices = (projects: Project[]) =>
  projects.map((project) => ({
    value: project.id,
    label: `${project.key} - ${project.title}${project.archivedAt ? " (archived)" : ""}`,
  }));

/**
 * The admin's list of users: who may sign in, to which projects, whether they set their
 * own password, and when they last signed in. `projects` holds every project, archived
 * ones included, so an assignment to an archived project is shown and kept.
 */
export const UsersDialog = ({
  opened,
  onClose,
  projects,
}: {
  opened: boolean;
  onClose: () => void;
  projects: Project[];
}) => {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<{
    username: string;
    projectIds: string[];
  }>({ username: "", projectIds: [] });
  const [credentials, setCredentials] = useState<Credentials>(null);
  const [projectEdit, setProjectEdit] = useState<ProjectEdit>(null);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);

  useEffect(() => {
    if (!opened) return;
    setError("");
    setCredentials(null);
    setProjectEdit(null);
    setConfirmation(null);
    fetchUsers()
      .then(setUsers)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load users"),
      );
  }, [opened]);

  /** Runs a change and reads the list again; a failure is shown, the list stays. */
  const run = async (change: () => Promise<void>, failure: string) => {
    setError("");
    try {
      await change();
      setUsers(await fetchUsers());
    } catch (err) {
      setError(err instanceof Error ? err.message : failure);
    }
  };

  const addUser = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const created = await api<UserCredentialsResponse>("/api/users", {
        method: "POST",
        body: JSON.stringify({
          username: draft.username,
          projectIds: draft.projectIds,
        }),
      });
      setCredentials({
        username: created.item.username,
        password: created.temporaryPassword,
        reason: "created",
      });
      setDraft({ username: "", projectIds: [] });
    }, "Failed to add the user");
  };

  const saveProjects = () => {
    if (!projectEdit) return;
    const { userId, projectIds } = projectEdit;
    void run(async () => {
      await api(`/api/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ projectIds }),
      });
      setProjectEdit(null);
    }, "Failed to save the projects");
  };

  const setActive = (user: UserSummary, active: boolean) => {
    void run(async () => {
      await api(`/api/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active }),
      });
    }, "Failed to change the user");
  };

  const confirmed = () => {
    if (!confirmation) return;
    const { action, user } = confirmation;
    setConfirmation(null);
    if (action === "delete") {
      void run(async () => {
        await api(`/api/users/${user.id}`, { method: "DELETE" });
      }, "Failed to delete the user");
      return;
    }
    void run(async () => {
      const reset = await api<UserCredentialsResponse>(
        `/api/users/${user.id}/reset-password`,
        { method: "POST" },
      );
      setCredentials({
        username: user.username,
        password: reset.temporaryPassword,
        reason: "reset",
      });
    }, "Failed to reset the password");
  };

  const choices = projectChoices(projects);
  const projectById = new Map(projects.map((project) => [project.id, project]));

  return (
    <>
      <Modal opened={opened} onClose={onClose} title="Users" centered size="xl">
        <Stack gap="lg">
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          {credentials && (
            <Paper className="token-created" withBorder radius="md" p="md">
              <Stack gap="sm">
                <Group align="center" justify="space-between">
                  <Title order={3}>{credentials.username}</Title>
                  <Badge variant="light" color="green">
                    {credentials.reason === "created"
                      ? "New User"
                      : "Password Reset"}
                  </Badge>
                </Group>
                <Text size="sm">
                  Temporary password, shown only this once. Pass it on:{" "}
                  {credentials.username} has to set a new one after signing in.
                </Text>
                <CopyableCode
                  value={credentials.password}
                  label="temporary password"
                  variant="block"
                />
              </Stack>
            </Paper>
          )}

          <Paper withBorder radius="md" p="md">
            <form className="modal-form" onSubmit={addUser}>
              <Stack gap="md">
                <Title order={3}>Add User</Title>
                <TextInput
                  label="Username"
                  description="3 to 64 characters: a-z, 0-9, dot, underscore or hyphen"
                  value={draft.username}
                  onChange={(event) => {
                    const username = event.currentTarget.value;
                    setDraft((current) => ({ ...current, username }));
                  }}
                  required
                />
                <MultiSelect
                  label="Projects"
                  description="The user gets full access to each of them"
                  value={draft.projectIds}
                  onChange={(projectIds) =>
                    setDraft((current) => ({ ...current, projectIds }))
                  }
                  data={choices}
                  searchable
                />
                <Group justify="flex-end">
                  <Button type="submit" leftSection={<UserPlus size={16} />}>
                    Add User
                  </Button>
                </Group>
              </Stack>
            </form>
          </Paper>

          <Stack className="token-list" gap="sm">
            {users.length === 0 && (
              <Paper
                className="empty-state inline"
                withBorder
                radius="md"
                p="md"
              >
                <Text c="dimmed">No users yet.</Text>
              </Paper>
            )}
            {users.map((user) => (
              <Paper
                component="section"
                aria-label={`User ${user.username}`}
                withBorder
                radius="md"
                p="md"
                key={user.id}
              >
                <Stack gap="sm">
                  <Group align="flex-start" justify="space-between" gap="md">
                    <Box>
                      <Group gap="xs">
                        <Title order={3}>{user.username}</Title>
                        {!user.active && (
                          <Badge variant="light" color="red">
                            Deactivated
                          </Badge>
                        )}
                        <Badge
                          variant="light"
                          color={user.mustChangePassword ? "yellow" : "green"}
                        >
                          {user.mustChangePassword
                            ? "Temporary password"
                            : "Password set"}
                        </Badge>
                      </Group>
                      <Text c="dimmed" size="sm" mt="xs">
                        {lastSignIn(user)}, created{" "}
                        {formatDateTime(user.createdAt)}
                      </Text>
                    </Box>
                    <Group gap="xs">
                      <Button
                        type="button"
                        variant="light"
                        color="gray"
                        size="xs"
                        leftSection={<Pencil size={14} />}
                        onClick={() =>
                          setProjectEdit({
                            userId: user.id,
                            projectIds: user.projectIds,
                          })
                        }
                      >
                        Edit Projects
                      </Button>
                      <Button
                        type="button"
                        variant="light"
                        color="gray"
                        size="xs"
                        leftSection={<KeyRound size={14} />}
                        onClick={() =>
                          setConfirmation({ action: "reset", user })
                        }
                      >
                        Reset Password
                      </Button>
                      <Button
                        type="button"
                        variant="light"
                        color={user.active ? "orange" : "teal"}
                        size="xs"
                        onClick={() => setActive(user, !user.active)}
                      >
                        {user.active ? "Deactivate" : "Activate"}
                      </Button>
                      <Button
                        type="button"
                        variant="light"
                        color="red"
                        size="xs"
                        leftSection={<Trash2 size={14} />}
                        onClick={() =>
                          setConfirmation({ action: "delete", user })
                        }
                      >
                        Delete
                      </Button>
                    </Group>
                  </Group>

                  {projectEdit?.userId === user.id ? (
                    <Stack gap="sm">
                      <MultiSelect
                        label="Projects"
                        value={projectEdit.projectIds}
                        onChange={(projectIds) =>
                          setProjectEdit({ userId: user.id, projectIds })
                        }
                        data={choices}
                        searchable
                      />
                      <Group justify="flex-end" gap="xs">
                        <Button
                          type="button"
                          variant="default"
                          size="xs"
                          onClick={() => setProjectEdit(null)}
                        >
                          Cancel
                        </Button>
                        <Button type="button" size="xs" onClick={saveProjects}>
                          Save Projects
                        </Button>
                      </Group>
                    </Stack>
                  ) : (
                    <Group gap={6}>
                      {user.projectIds.length === 0 && (
                        <Text c="dimmed" size="sm">
                          No projects
                        </Text>
                      )}
                      {user.projectIds.map((projectId) => {
                        const project = projectById.get(projectId);
                        return (
                          <Badge variant="light" color="gray" key={projectId}>
                            {project ? projectBadge(project) : projectId}
                          </Badge>
                        );
                      })}
                    </Group>
                  )}
                </Stack>
              </Paper>
            ))}
          </Stack>
        </Stack>
      </Modal>

      <Modal
        opened={confirmation !== null}
        onClose={() => setConfirmation(null)}
        title={
          confirmation?.action === "delete" ? "Delete User" : "Reset Password"
        }
        centered
        size="sm"
      >
        {/* Empty while the dialog closes, so no half sentence shows on the way out. */}
        {confirmation && (
          <Stack gap="md">
            <Text>
              {confirmation.action === "delete"
                ? `Delete ${confirmation.user.username} for good? Tasks and comments they wrote stay.`
                : `Reset the password of ${confirmation.user.username}? Their current password stops working at once and they are signed out.`}
            </Text>
            <Group justify="flex-end" gap="xs">
              <Button
                type="button"
                variant="default"
                onClick={() => setConfirmation(null)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                color={confirmation.action === "delete" ? "red" : "orange"}
                onClick={confirmed}
              >
                {confirmation.action === "delete" ? "Delete User" : "Reset Now"}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </>
  );
};
