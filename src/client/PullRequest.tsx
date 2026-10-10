import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from "@mantine/core";
import {
  Cloud,
  GitPullRequest,
  HardDrive,
  Link,
  Link2,
  Pencil,
  Upload,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import type { BoardCard, PushRequest } from "../shared/types.js";
import { api } from "./api.js";
import { CopyableCode } from "./CopyableCode.js";
import { MarkdownText } from "./Markdown.js";
import { MarkdownField, MarkdownPreview } from "./MarkdownField.js";
import type { MarkdownFieldMode } from "./markdownField.js";

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

/**
 * Whether the card's branch is on GitHub as far as the board knows: a worker
 * reported pushing it, at the card's head commit when it has one.
 */
export const branchPushed = (
  card: Pick<BoardCard, "branchPushedSha" | "headSha">,
): boolean => {
  const pushed = card.branchPushedSha;
  if (!pushed) return false;
  const head = card.headSha;
  return !head || head.startsWith(pushed) || pushed.startsWith(head);
};

/** "on GitHub" once a worker pushed the card's head commit, else "local only". */
export const BranchPushBadge = ({
  card,
}: {
  card: Pick<BoardCard, "branchPushedSha" | "branchPushedAt" | "headSha">;
}) => {
  const pushed = branchPushed(card);
  return (
    <Badge
      className="branch-push-badge"
      variant="light"
      color={pushed ? "green" : "gray"}
      size="sm"
      leftSection={
        pushed ? (
          <Cloud size={12} aria-hidden />
        ) : (
          <HardDrive size={12} aria-hidden />
        )
      }
      title={
        pushed && card.branchPushedSha
          ? `Pushed ${card.branchPushedSha.slice(0, 12)}${
              card.branchPushedAt
                ? ` on ${formatSuggestedAt(card.branchPushedAt)}`
                : ""
            }`
          : "Not pushed to GitHub by a worker yet"
      }
      style={{ textTransform: "none" }}
    >
      {pushed ? "on GitHub" : "local only"}
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
  /** False while the card's branch is not on GitHub. */
  branchOnGithub?: boolean;
};

const linkPath = (card: BoardCard) => `/api/board/${card.id}/pull-request`;
const pushPath = (card: BoardCard) => `/api/board/${card.id}/push-request`;
/** How often an open push request is polled while the dialog shows it. */
export const pushPollMs = 2000;

const isActivePush = (request: PushRequest | null) =>
  request?.status === "pending" || request?.status === "claimed";

/** The commit a push request is for: its short sha, or the branch tip when unknown. */
const PushTarget = ({ request }: { request: PushRequest }) =>
  request.headSha ? (
    <>
      <code>{request.branch}</code> at{" "}
      <code>{request.headSha.slice(0, 12)}</code>
    </>
  ) : (
    <>
      the tip of <code>{request.branch}</code>
    </>
  );

/** Where a push request stands, for the Open PR dialog. */
const PushProgress = ({ request }: { request: PushRequest }) => {
  if (request.status === "pending") {
    return (
      <Alert color="blue" variant="light" title="Waiting for a worker">
        A worker of the project that has the repository pushes{" "}
        <PushTarget request={request} /> with its next heartbeat.
      </Alert>
    );
  }
  if (request.status === "claimed") {
    return (
      <Alert color="blue" variant="light" title="Pushing">
        {request.assignee ?? "A worker"} is pushing{" "}
        <PushTarget request={request} />.
      </Alert>
    );
  }
  if (request.status === "failed") {
    return (
      <Alert color="red" variant="light" title="The push failed">
        {request.error ?? "The worker could not push the branch."}
      </Alert>
    );
  }
  if (request.pullRequestUrl) {
    return (
      <Alert color="green" variant="light" title="Pull request opened">
        <code>{request.branch}</code> was pushed and the pull request is open:{" "}
        <a href={request.pullRequestUrl} target="_blank" rel="noreferrer">
          {request.pullRequestUrl}
        </a>
      </Alert>
    );
  }
  return (
    <Alert color="orange" variant="light" title="Pushed">
      {request.error ?? `${request.branch} was pushed.`}
    </Alert>
  );
};

/** Opens the pull request for the card's branch; the fields start from the server's draft. */
const OpenPullRequestDialog = ({
  card,
  liveWorkers,
  onClose,
  onOpen,
}: {
  card: BoardCard;
  liveWorkers: number;
  onClose: () => void;
  onOpen: () => Promise<void>;
}) => {
  const [draft, setDraft] = useState<PullRequestDraft | null>(null);
  const [push, setPush] = useState<PushRequest | null>(null);
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
    setPush(null);
    setError("");
    setIsSubmitting(false);
    // The draft comes back as is, not wrapped in `item` like the card writes.
    api<PullRequestDraft>(linkPath(card))
      .then(async (draft) => {
        if (cancelled) return;
        setDraft(draft);
        setTitle(draft.title);
        setBase(draft.base);
        setBody(draft.body);
        setIsDraft(false);
        if (draft.branchOnGithub !== false) return;
        // A push asked for earlier may still be on its way.
        const latest = await api<PushRequest>(pushPath(card)).catch(() => null);
        if (!cancelled && isActivePush(latest)) setPush(latest);
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

  const pushing = isActivePush(push);
  // biome-ignore lint/correctness/useExhaustiveDependencies: polls while the request is open
  useEffect(() => {
    if (!push || !pushing) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api<PushRequest>(pushPath(card))
        .then(async (latest) => {
          if (cancelled) return;
          setPush(latest);
          if (latest.status === "pushed") await onOpen();
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : "Request failed");
          }
        });
    }, pushPollMs);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [push, pushing]);

  const branchMissing = draft?.branchOnGithub === false;
  const canPush = branchMissing && liveWorkers > 0 && push?.status !== "pushed";
  const pushCommand = `git push origin ${card.branchName ?? ""}`;

  const requestPush = async () => {
    setIsSubmitting(true);
    setError("");
    try {
      const request = await api<PushRequest>(pushPath(card), {
        method: "POST",
        body: JSON.stringify({
          openPr: { title, base, body, draft: isDraft },
        }),
      });
      setPush(request);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (canPush) {
      await requestPush();
      return;
    }
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
              {push && <PushProgress request={push} />}
              {branchMissing && !push && liveWorkers > 0 && (
                <Alert color="yellow" variant="light">
                  <code>{card.branchName}</code> is not on GitHub yet. Push and
                  open PR asks a worker of the project to push it; the pull
                  request opens once it is pushed.
                </Alert>
              )}
              {branchMissing &&
                (liveWorkers === 0 || push?.status === "failed") && (
                  <Alert color="yellow" variant="light">
                    <Stack gap="xs">
                      <Text size="sm">
                        <code>{card.branchName}</code> is not on GitHub yet
                        {liveWorkers === 0 ? " and no worker is online" : ""}.
                        Push it from the repository, then open the pull request:
                      </Text>
                      <CopyableCode
                        value={pushCommand}
                        label="push command"
                        variant="block"
                      />
                    </Stack>
                  </Alert>
                )}
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
            {push?.status === "pushed" ? (
              <Button type="button" onClick={onClose}>
                Done
              </Button>
            ) : (
              <Button
                type="submit"
                leftSection={
                  canPush ? (
                    <Upload size={14} aria-hidden />
                  ) : (
                    <GitPullRequest size={14} aria-hidden />
                  )
                }
                loading={isSubmitting || pushing}
                disabled={!draft || !title.trim() || !base}
              >
                {canPush ? "Push and open PR" : "Open pull request"}
              </Button>
            )}
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
  liveWorkers = 0,
  onChange,
}: {
  card: BoardCard;
  enabled: boolean;
  /** Connected workers of the project, which can push a missing branch. */
  liveWorkers?: number;
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
          liveWorkers={liveWorkers}
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

const formatSuggestedAt = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

/**
 * The pull request title and description suggested for the card, usually by the
 * agent at hand-over. Open PR starts from them; they can be edited here.
 */
export const SuggestedPullRequest = ({
  card,
  editable,
  onChange,
}: {
  card: BoardCard;
  editable: boolean;
  onChange: () => Promise<void>;
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [bodyMode, setBodyMode] = useState<MarkdownFieldMode>("edit");
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const hasSuggestion = Boolean(card.suggestedPrTitle || card.suggestedPrBody);

  const edit = () => {
    setTitle(card.suggestedPrTitle ?? "");
    setBody(card.suggestedPrBody ?? "");
    setBodyMode("edit");
    setError("");
    setIsEditing(true);
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    setError("");
    try {
      await api<{ item: BoardCard }>(`/api/board/${card.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          prTitle: title,
          prBody: body,
          expectedUpdatedAt: card.updatedAt,
        }),
      });
      await onChange();
      setIsEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Paper className="overview-section" withBorder radius="md" p="md">
      <Group align="center" justify="space-between" mb="xs">
        <Text size="xs" fw={700} tt="uppercase" c="dimmed">
          Pull request
        </Text>
        {editable && !isEditing && (
          <Button
            type="button"
            size="xs"
            variant="subtle"
            leftSection={<Pencil size={14} aria-hidden />}
            onClick={edit}
          >
            {hasSuggestion ? "Edit" : "Add"}
          </Button>
        )}
      </Group>
      {isEditing ? (
        <form onSubmit={(event) => void save(event)}>
          <Stack gap="sm">
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <TextInput
              label="Title"
              value={title}
              onChange={(event) => setTitle(event.currentTarget.value)}
              data-autofocus
            />
            <MarkdownField
              label="Description"
              mode={bodyMode}
              onModeChange={setBodyMode}
              preview={
                <MarkdownPreview value={body} emptyText="No description." />
              }
            >
              <Textarea
                aria-label="Description"
                value={body}
                onChange={(event) => setBody(event.currentTarget.value)}
                autosize
                minRows={6}
              />
            </MarkdownField>
            <Group justify="flex-end">
              <Button
                type="button"
                variant="default"
                disabled={isSaving}
                onClick={() => setIsEditing(false)}
              >
                Cancel
              </Button>
              <Button type="submit" loading={isSaving}>
                Save
              </Button>
            </Group>
          </Stack>
        </form>
      ) : hasSuggestion ? (
        <Stack gap="xs">
          {card.suggestedPrTitle && (
            <Text fw={600}>{card.suggestedPrTitle}</Text>
          )}
          {card.suggestedPrBody && (
            <div className="overview-markdown">
              <MarkdownText>{card.suggestedPrBody}</MarkdownText>
            </div>
          )}
          {card.suggestedPrBy && card.suggestedPrAt && (
            <Text size="xs" c="dimmed">
              Suggested by {card.suggestedPrBy} on{" "}
              {formatSuggestedAt(card.suggestedPrAt)}
            </Text>
          )}
        </Stack>
      ) : (
        <Text c="dimmed" size="sm">
          No suggested description yet. The agent suggests one when it hands the
          task over; Open PR starts from it.
        </Text>
      )}
    </Paper>
  );
};
