import {
  Alert,
  Button,
  Group,
  Modal,
  Stack,
  Text,
  Textarea,
} from "@mantine/core";
import { type FormEvent, useEffect, useState } from "react";
import type { BoardCard } from "../shared/types.js";

type TextDialogProps = {
  card: BoardCard | null;
  title: string;
  label: string;
  intro?: string;
  submitLabel: string;
  onClose: () => void;
  onSubmit: (card: BoardCard, text: string) => Promise<void>;
};

/** A dialog asking for one piece of text about a card, sent on submit. */
const TextDialog = ({
  card,
  title,
  label,
  intro,
  submitLabel,
  onClose,
  onSubmit,
}: TextDialogProps) => {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new card starts a new draft
  useEffect(() => {
    setText("");
    setError("");
    setIsSubmitting(false);
  }, [card?.id]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!card || !text.trim()) return;
    setIsSubmitting(true);
    setError("");
    try {
      await onSubmit(card, text.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      opened={Boolean(card)}
      onClose={() => {
        if (!isSubmitting) onClose();
      }}
      title={title}
      centered
      size="lg"
    >
      {card && (
        <form className="modal-form" onSubmit={(event) => void submit(event)}>
          <Stack gap="md">
            {intro && <Text size="sm">{intro}</Text>}
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <Textarea
              label={label}
              value={text}
              onChange={(event) => setText(event.currentTarget.value)}
              autosize
              minRows={4}
              required
              data-autofocus
            />
            <Group justify="flex-end">
              <Button
                type="button"
                variant="default"
                disabled={isSubmitting}
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                loading={isSubmitting}
                disabled={!text.trim()}
              >
                {submitLabel}
              </Button>
            </Group>
          </Stack>
        </form>
      )}
    </Modal>
  );
};

type CardDialogProps = {
  card: BoardCard | null;
  onClose: () => void;
  onSubmit: (card: BoardCard, text: string) => Promise<void>;
};

/** Answers the question an automated run asked; the task goes back to Planned. */
export const AnswerDialog = ({ card, onClose, onSubmit }: CardDialogProps) => (
  <TextDialog
    card={card}
    title={card ? `Answer: ${card.title}` : "Answer"}
    label="Answer"
    intro={card?.question}
    submitLabel="Answer and plan again"
    onClose={onClose}
    onSubmit={onSubmit}
  />
);

/**
 * Sends a reviewed task back to Planned with feedback; the next run continues
 * on its branch.
 */
export const ReworkDialog = ({ card, onClose, onSubmit }: CardDialogProps) => (
  <TextDialog
    card={card}
    title={card ? `Request changes: ${card.title}` : "Request changes"}
    label="Feedback"
    intro={
      card?.branchName
        ? `The task goes back to Planned; the next run continues on ${card.branchName} with this feedback.`
        : "The task goes back to Planned; the next run gets this feedback."
    }
    submitLabel="Send back to Planned"
    onClose={onClose}
    onSubmit={onSubmit}
  />
);
