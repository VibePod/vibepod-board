import {
  Alert,
  Button,
  Group,
  Modal,
  Radio,
  Stack,
  Text,
  Textarea,
} from "@mantine/core";
import { type FormEvent, useEffect, useState } from "react";
import {
  type BoardCard,
  type RebaseTarget,
  rebaseTargets,
} from "../shared/types.js";
import { rebaseTargetLabels } from "./rebaseUtils.js";

type TextDialogProps = {
  card: BoardCard | null;
  title: string;
  label: string;
  intro?: string;
  submitLabel: string;
  optional?: boolean;
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
  optional = false,
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
    if (!card || (!optional && !text.trim())) return;
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
              required={!optional}
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
                disabled={!optional && !text.trim()}
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

/**
 * Answers the question an automated run asked; the task goes back to Planned.
 * A rebase worker's question is answered in the card's column, and the rebase
 * is taken up again.
 */
export const AnswerDialog = ({ card, onClose, onSubmit }: CardDialogProps) => (
  <TextDialog
    card={card}
    title={card ? `Answer: ${card.title}` : "Answer"}
    label="Answer"
    intro={card?.question}
    submitLabel={
      card?.rebaseTarget
        ? "Answer and resume the rebase"
        : "Answer and plan again"
    }
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

export const ApproveDialog = ({ card, onClose, onSubmit }: CardDialogProps) => (
  <TextDialog
    card={card}
    title="Approve review"
    label="Note (optional)"
    submitLabel="Approve"
    optional
    onClose={onClose}
    onSubmit={onSubmit}
  />
);

type RebaseDialogProps = {
  card: BoardCard | null;
  onClose: () => void;
  onSubmit: (card: BoardCard, target: RebaseTarget) => Promise<void>;
};

/**
 * Asks a rebase worker to rebase the card's branch onto origin's task branch,
 * the default branch, or both. The card stays in its column, unblocked.
 */
export const RebaseDialog = ({
  card,
  onClose,
  onSubmit,
}: RebaseDialogProps) => {
  const [target, setTarget] = useState<RebaseTarget>("both");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new card starts a new request
  useEffect(() => {
    setTarget(card?.rebaseTarget ?? "both");
    setError("");
    setIsSubmitting(false);
  }, [card?.id]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!card) return;
    setIsSubmitting(true);
    setError("");
    try {
      await onSubmit(card, target);
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
      title={card ? `Rebase: ${card.title}` : "Rebase"}
      centered
      size="lg"
    >
      {card && (
        <form className="modal-form" onSubmit={(event) => void submit(event)}>
          <Stack gap="md">
            <Text size="sm">
              A rebase worker rebases {card.branchName ?? "the task's branch"}{" "}
              and reports the new head, or asks when it cannot go on alone. The
              card stays in its column
              {card.blockedAt ? ", and its block is cleared meanwhile" : ""}.
            </Text>
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <Radio.Group
              label="Rebase onto"
              value={target}
              onChange={(value) => setTarget(value as RebaseTarget)}
            >
              <Stack gap="xs" mt="xs">
                {rebaseTargets.map((option) => (
                  <Radio
                    key={option}
                    value={option}
                    label={rebaseTargetLabels[option]}
                  />
                ))}
              </Stack>
            </Radio.Group>
            <Group justify="flex-end">
              <Button
                type="button"
                variant="default"
                disabled={isSubmitting}
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button type="submit" loading={isSubmitting} data-autofocus>
                Request rebase
              </Button>
            </Group>
          </Stack>
        </form>
      )}
    </Modal>
  );
};
