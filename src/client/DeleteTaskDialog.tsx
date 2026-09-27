import { Alert, Button, Group, List, Modal, Stack, Text } from "@mantine/core";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import type { Idea } from "../shared/types.js";
import { issueLinkForIdea } from "./githubIssue.js";

export type DeleteTaskTarget = {
  idea: Idea;
  taskId: string;
};

/** Consequences listed before a permanent delete, so nothing disappears unannounced. */
export const deleteTaskConsequences = (idea: Idea): string[] => {
  const consequences = [
    "Removes its board card, dependency links and readiness history.",
  ];
  if (idea.blocks.length > 0) {
    consequences.push(
      idea.blocks.length === 1
        ? "1 task depends on it and will lose this dependency."
        : `${idea.blocks.length} tasks depend on it and will lose this dependency.`,
    );
  }
  const issue = issueLinkForIdea(idea);
  if (issue) {
    const label = issue.repository
      ? `${issue.repository}#${issue.number}`
      : `#${issue.number}`;
    consequences.push(`The linked GitHub issue ${label} is not touched.`);
  }
  return consequences;
};

export const DeleteTaskDialog = ({
  target,
  onCancel,
  onConfirm,
}: {
  target: DeleteTaskTarget | null;
  onCancel: () => void;
  onConfirm: (target: DeleteTaskTarget) => Promise<void>;
}) => {
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState("");

  const cancel = () => {
    if (isDeleting) return;
    setError("");
    onCancel();
  };

  const confirm = async () => {
    if (!target) return;
    setIsDeleting(true);
    setError("");
    try {
      await onConfirm(target);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete task");
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <Modal
      opened={!!target}
      onClose={cancel}
      title="Delete Task"
      centered
      closeOnClickOutside={!isDeleting}
      closeOnEscape={!isDeleting}
    >
      {target && (
        <Stack gap="md">
          <Text>
            Delete <strong>{target.taskId}</strong> · {target.idea.title}? This
            cannot be undone.
          </Text>
          <List size="sm" spacing={4}>
            {deleteTaskConsequences(target.idea).map((line) => (
              <List.Item key={line}>{line}</List.Item>
            ))}
          </List>
          <Text size="sm" c="dimmed">
            To reject the work but keep the record, set its status to Denied
            instead.
          </Text>
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
              type="button"
              color="red"
              leftSection={<Trash2 size={16} />}
              loading={isDeleting}
              onClick={() => void confirm()}
            >
              Delete Task
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
};
