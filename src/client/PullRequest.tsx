import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { GitPullRequest, Link, Link2 } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import type { BoardCard } from "../shared/types.js";
import { api } from "./api.js";

/** Opens the linked pull request in a new tab; green while open, violet once merged. */
export const PullRequestBadge = ({
  card,
  size = "sm",
}: {
  card: Pick<
    BoardCard,
    | "githubPrUrl"
    | "githubPrNumber"
    | "githubPrRepository"
    | "githubPrState"
    | "githubPrDraft"
  >;
  size?: "xs" | "sm";
}) => {
  const { githubPrUrl: url, githubPrNumber: number } = card;
  if (!url || number === undefined || number === null) return null;
  const state = card.githubPrState;
  const label = card.githubPrRepository
    ? `${card.githubPrRepository}#${number}`
    : `#${number}`;
  return (
    <Badge
      component="a"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      variant="light"
      color={
        state === "open" ? "green" : state === "merged" ? "violet" : "gray"
      }
      size={size}
      leftSection={<GitPullRequest size={12} aria-hidden />}
      title={`Open pull request ${label} on GitHub${state ? ` (${state})` : ""}`}
      aria-label={`Open pull request ${label} in a new tab`}
      style={{ cursor: "pointer", textTransform: "none" }}
      // Cards open the task on click; following the link must not do that too.
      onClick={(event) => event.stopPropagation()}
    >
      {card.githubPrDraft
        ? `#${number} draft`
        : `#${number}${state ? ` ${state}` : ""}`}
    </Badge>
  );
};

type PullRequestDraft = {
  repository: string;
  title: string;
  base: string;
  bases: string[];
  body: string;
  draft: boolean;
};

const linkPath = (card: BoardCard) => `/api/board/${card.id}/pull-request`;

/** Opens the pull request for the card's branch; the fields start from the server's draft. */
const OpenPullRequestDialog = ({
  card,
  onClose,
  onOpen,
}: {
  card: BoardCard;
  onClose: () => void;
  onOpen: () => Promise<void>;
}) => {
  const [draft, setDraft] = useState<PullRequestDraft | null>(null);
  const [title, setTitle] = useState("");
  const [base, setBase] = useState("");
  const [body, setBody] = useState("");
  const [isDraft, setIsDraft] = useState(false);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new card starts a new draft
  useEffect(() => {
    let cancelled = false;
    setDraft(null);
    setError("");
    setIsSubmitting(false);
    // The draft comes back as is, not wrapped in `item` like the card writes.
    api<PullRequestDraft>(linkPath(card))
      .then((draft) => {
        if (cancelled) return;
        setDraft(draft);
        setTitle(draft.title);
        setBase(draft.base);
        setBody(draft.body);
        setIsDraft(false);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Request failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [card.id]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError("");
    try {
      await api<{ item: BoardCard }>(linkPath(card), {
        method: "POST",
        body: JSON.stringify({ title, base, body, draft: isDraft }),
      });
      await onOpen();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      opened
      onClose={() => {
        if (!isSubmitting) onClose();
      }}
      title="Open Pull Request"
      centered
      size="lg"
    >
      <form className="modal-form" onSubmit={(event) => void submit(event)}>
        <Stack gap="md">
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}
          {draft && (
            <>
              <Text size="sm" c="dimmed">
                From <code>{card.branchName}</code> into{" "}
                <code>{draft.repository}</code>.
              </Text>
              <TextInput
                label="Title"
                value={title}
                onChange={(event) => setTitle(event.currentTarget.value)}
                required
                data-autofocus
              />
              <Select
                label="Base"
                data={draft.bases}
                value={base}
                onChange={(value) => setBase(value ?? "")}
                required
              />
              <Textarea
                label="Body"
                value={body}
                onChange={(event) => setBody(event.currentTarget.value)}
                autosize
                minRows={6}
              />
              <Switch
                label="Draft"
                description="Open as a draft pull request"
                checked={isDraft}
                onChange={(event) => setIsDraft(event.currentTarget.checked)}
              />
            </>
          )}
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
              leftSection={<GitPullRequest size={14} aria-hidden />}
              loading={isSubmitting}
              disabled={!draft || !title.trim() || !base}
            >
              Open pull request
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
};

/** Links a pull request opened by hand by its URL; an empty URL unlinks it. */
const LinkPullRequestDialog = ({
  card,
  onClose,
  onOpen,
}: {
  card: BoardCard;
  onClose: () => void;
  onOpen: () => Promise<void>;
}) => {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setError("");
    try {
      await api<{ item: BoardCard }>(`${linkPath(card)}/link`, {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      await onOpen();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      opened
      onClose={() => {
        if (!isSubmitting) onClose();
      }}
      title={
        card.githubPrNumber
          ? "Link or Unlink Pull Request"
          : "Link Pull Request"
      }
      centered
      size="lg"
    >
      <form className="modal-form" onSubmit={(event) => void submit(event)}>
        <Stack gap="md">
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}
          <TextInput
            label="Pull request URL"
            description={
              card.githubPrNumber
                ? "https://github.com/owner/repo/pull/123, or empty to unlink the current PR"
                : "https://github.com/owner/repo/pull/123"
            }
            placeholder={
              card.githubPrUrl ?? "https://github.com/owner/repo/pull/123"
            }
            value={url}
            onChange={(event) => setUrl(event.currentTarget.value)}
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
            <Button type="submit" loading={isSubmitting}>
              {card.githubPrNumber ? "Save" : "Link"}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
};

/** The card's pull request: badge, Open PR for cards in PR ready, and Link PR. */
export const PullRequestPanel = ({
  card,
  enabled,
  onChange,
}: {
  card: BoardCard;
  enabled: boolean;
  onChange: () => Promise<void>;
}) => {
  const [openDialog, setOpenDialog] = useState<"open" | "link" | null>(null);
  const hasLivePr =
    card.githubPrNumber !== undefined &&
    card.githubPrNumber !== null &&
    card.githubPrState !== "closed";
  const canOpen =
    enabled &&
    card.column === "pr_ready" &&
    Boolean(card.branchName) &&
    !hasLivePr;

  return (
    <Group gap="sm" align="center" wrap="wrap">
      <PullRequestBadge card={card} />
      <Tooltip
        label={
          !enabled
            ? "Set GITHUB_TOKEN to open pull requests"
            : !card.branchName
              ? "Set the card's branch first"
              : hasLivePr
                ? "Unlink the current pull request first"
                : "Only cards in PR ready can open a pull request"
        }
        disabled={canOpen}
      >
        <Button
          type="button"
          size="xs"
          variant="light"
          leftSection={<GitPullRequest size={14} aria-hidden />}
          disabled={!canOpen}
          onClick={() => setOpenDialog("open")}
        >
          Open PR
        </Button>
      </Tooltip>
      <Tooltip
        label={
          card.githubPrNumber
            ? "Unlink or replace the pull request"
            : "Link a pull request by URL"
        }
      >
        <Button
          type="button"
          size="xs"
          variant="default"
          leftSection={
            card.githubPrNumber ? (
              <Link size={14} aria-hidden />
            ) : (
              <Link2 size={14} aria-hidden />
            )
          }
          onClick={() => setOpenDialog("link")}
        >
          Link PR
        </Button>
      </Tooltip>
      {openDialog === "open" && (
        <OpenPullRequestDialog
          card={card}
          onClose={() => setOpenDialog(null)}
          onOpen={onChange}
        />
      )}
      {openDialog === "link" && (
        <LinkPullRequestDialog
          card={card}
          onClose={() => setOpenDialog(null)}
          onOpen={onChange}
        />
      )}
    </Group>
  );
};
