import { Alert, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { Archive } from "lucide-react";
import { useState } from "react";

export type ArchiveDoneTarget = {
  projectId: string;
  projectTitle: string;
  count: number;
};

/** Confirms archiving the whole Done column, the same way a delete is confirmed. */
export const ArchiveDoneDialog = ({
  target,
  onCancel,
  onConfirm,
}: {
  target: ArchiveDoneTarget | null;
  onCancel: () => void;
  onConfirm: (target: ArchiveDoneTarget) => Promise<void>;
}) => {
  const [isArchiving, setIsArchiving] = useState(false);
  const [error, setError] = useState("");

  const cancel = () => {
    if (isArchiving) return;
    setError("");
    onCancel();
  };

  const confirm = async () => {
    if (!target) return;
    setIsArchiving(true);
    setError("");
    try {
      await onConfirm(target);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive cards");
    } finally {
      setIsArchiving(false);
    }
  };

  return (
    <Modal
      opened={!!target}
      onClose={cancel}
      title="Archive Done Cards"
      centered
      closeOnClickOutside={!isArchiving}
      closeOnEscape={!isArchiving}
    >
      {target && (
        <Stack gap="md">
          <Text>
            Archive{" "}
            <strong>
              {target.count === 1
                ? "1 done card"
                : `${target.count} done cards`}
            </strong>{" "}
            in {target.projectTitle}?
          </Text>
          <Text size="sm" c="dimmed">
            Archived cards leave the board but keep their tasks, dependencies,
            readiness history and GitHub links. Restore them from the Archive
            view at any time.
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
              disabled={isArchiving}
            >
              Cancel
            </Button>
            <Button
              type="button"
              leftSection={<Archive size={16} />}
              loading={isArchiving}
              onClick={() => void confirm()}
            >
              Archive Cards
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
};
