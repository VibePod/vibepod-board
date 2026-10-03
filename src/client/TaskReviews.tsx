import { Badge, Button, Code, Group, Paper, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import type { ReviewState } from "../shared/types.js";
import { reviewVerdictColors, reviewVerdictLabels } from "./automationUtils.js";
import { api } from "./api.js";
import { MarkdownText } from "./Markdown.js";

/** Both views read the same authoritative approvals and open review claims. */
export const TaskReviews = ({
  cardId,
  reloadKey,
  compact = false,
  onChanged,
}: {
  cardId: string;
  reloadKey: string;
  compact?: boolean;
  onChanged?: () => Promise<void>;
}) => {
  const [loaded, setLoaded] = useState<{
    cardId: string;
    state: ReviewState;
  } | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [cancelling, setCancelling] = useState<string | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey and version trigger a refresh
  useEffect(() => {
    let disposed = false;
    setError("");
    let request = 0;
    const refresh = () => {
      const currentRequest = ++request;
      api<ReviewState>(`/api/board/${cardId}/reviews`)
        .then((state) => {
          if (!Array.isArray(state.items))
            throw new Error("Invalid review response");
          if (!disposed && currentRequest === request) {
            setLoaded({ cardId, state });
            setError("");
          }
        })
        .catch((err: Error) => {
          if (!disposed && currentRequest === request) setError(err.message);
        });
    };
    refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 15000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [cardId, reloadKey, version]);
  const state = loaded?.cardId === cardId ? loaded.state : null;
  const cancel = async (id: string) => {
    setCancelling(id);
    try {
      await api(`/api/board/${cardId}/reviews/${id}/cancel`, {
        method: "POST",
        body: "{}",
      });
      setVersion((v) => v + 1);
      await onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setCancelling(null);
    }
  };
  const content = (
    <Stack gap="xs">
      {error && (
        <Text size="xs" c="red">
          Reviews unavailable: {error}
        </Text>
      )}
      {state && (
        <>
          <Group gap="xs">
            <Badge color="teal" variant="light">
              {state.approvals}/{state.requiredApprovals} approvals
            </Badge>
            <Text size="xs" c="dimmed">
              Review rounds: {state.reviewRounds}/{state.maxReviewRounds}
            </Text>
          </Group>
          {(compact
            ? state.items.filter((review) => review.open)
            : state.items
          ).map((review) => (
            <Stack gap={3} key={review.id}>
              <Group gap="xs">
                <Text size="sm" fw={600}>
                  {review.reviewer}
                </Text>
                {review.headSha && <Code>{review.headSha.slice(0, 8)}</Code>}
                <Badge
                  variant="light"
                  color={
                    review.verdict
                      ? reviewVerdictColors[review.verdict]
                      : "gray"
                  }
                >
                  {review.open
                    ? "Reviewing"
                    : review.verdict
                      ? reviewVerdictLabels[review.verdict]
                      : "Ended"}
                </Badge>
                {!compact && review.headSha !== state.headSha && (
                  <Badge color="orange">Outdated</Badge>
                )}
                {review.open && (
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="red"
                    loading={cancelling === review.id}
                    disabled={cancelling !== null}
                    aria-label={`Cancel review by ${review.reviewer}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      void cancel(review.id);
                    }}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    Cancel
                  </Button>
                )}
              </Group>
              {!compact && (
                <>
                  <Text size="xs" c="dimmed">
                    {new Date(
                      review.endedAt ?? review.createdAt,
                    ).toLocaleString()}
                  </Text>
                  {(review.feedback || review.endedReason) && (
                    <MarkdownText>
                      {review.feedback || review.endedReason || ""}
                    </MarkdownText>
                  )}
                </>
              )}
            </Stack>
          ))}
        </>
      )}
    </Stack>
  );
  return compact ? (
    <div>{content}</div>
  ) : (
    <Paper
      className="overview-section task-reviews"
      withBorder
      radius="md"
      p="md"
    >
      <Text size="xs" fw={700} tt="uppercase" c="dimmed" mb="xs">
        Reviews
      </Text>
      {content}
    </Paper>
  );
};
