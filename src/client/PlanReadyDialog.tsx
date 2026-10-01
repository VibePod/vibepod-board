import { Alert, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { Send } from "lucide-react";
import { useState } from "react";

import type { BoardCard } from "../shared/types.js";

/** The most cards one batch update takes; the API refuses a larger batch. */
export const planReadyLimit = 50;

export type PlanReadyTarget = {
  projectTitle: string;
  /** The Ready cards as the board showed them, in column order. */
  cards: BoardCard[];
};

/** Confirms planning the whole Ready column, the same way archiving Done is confirmed. */
export const PlanReadyDialog = ({
  target,
  onCancel,
  onConfirm,
}: {
  target: PlanReadyTarget | null;
  onCancel: () => void;
  onConfirm: (target: PlanReadyTarget) => Promise<void>;
}) => {
  const [isPlanning, setIsPlanning] = useState(false);
  const [error, setError] = useState("");

  const cancel = () => {
    if (isPlanning) return;
    setError("");
    onCancel();
  };

  const confirm = async () => {
    if (!target) return;
    setIsPlanning(true);
    setError("");
    try {
      await onConfirm(target);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to plan cards");
    } finally {
      setIsPlanning(false);
    }
  };

  const count = Math.min(target?.cards.length ?? 0, planReadyLimit);

  return (
    <Modal
      opened={!!target}
      onClose={cancel}
      title="Plan Ready Tasks"
      centered
      closeOnClickOutside={!isPlanning}
      closeOnEscape={!isPlanning}
    >
      {target && (
        <Stack gap="md">
          <Text>
            Plan{" "}
            <strong>
              {count === 1 ? "1 ready task" : `${count} ready tasks`}
            </strong>{" "}
            in {target.projectTitle}?
          </Text>
          <Text size="sm" c="dimmed">
            Planned tasks go to the workers. A task that depends on unfinished
            tasks waits for them.
          </Text>
          {target.cards.length > planReadyLimit && (
            <Text size="sm" c="dimmed">
              Only the first {planReadyLimit} of {target.cards.length} are
              planned at once.
            </Text>
          )}
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
              disabled={isPlanning}
            >
              Cancel
            </Button>
            <Button
              type="button"
              leftSection={<Send size={16} />}
              loading={isPlanning}
              onClick={() => void confirm()}
            >
              Plan Tasks
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
};
