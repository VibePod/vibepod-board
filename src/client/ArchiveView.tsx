import {
  Badge,
  Box,
  Button,
  Card,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { ArchiveRestore, GitBranch } from "lucide-react";
import { useMemo, useState } from "react";
import {
  type ArchivedTaskRow,
  type ArchiveSortOption,
  archiveSortOptions,
  filterAndSortArchivedTasks,
} from "./archiveUtils.js";

const formatArchivedAt = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

/** Archived tasks of one project, with search, sort and a way back to Done. */
export const ArchiveView = ({
  rows,
  onOpen,
  onUnarchive,
}: {
  rows: ArchivedTaskRow[];
  onOpen: (row: ArchivedTaskRow) => void;
  onUnarchive: (row: ArchivedTaskRow) => Promise<void>;
}) => {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ArchiveSortOption>("archived_desc");
  const [restoringId, setRestoringId] = useState("");
  const visibleRows = useMemo(
    () => filterAndSortArchivedTasks(rows, { search, sort }),
    [rows, search, sort],
  );

  const unarchive = async (row: ArchivedTaskRow) => {
    setRestoringId(row.cardId);
    try {
      await onUnarchive(row);
    } finally {
      setRestoringId("");
    }
  };

  return (
    <Stack gap="sm">
      <Paper className="task-filters" withBorder radius="md" p="md">
        <Text size="sm" fw={600} c="dimmed" mb="sm">
          {visibleRows.length} of {rows.length} archived tasks
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <TextInput
            label="Search"
            placeholder="Search task ID, title, labels, branch"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
          />
          <Select
            label="Sort"
            value={sort}
            onChange={(value) =>
              setSort((value ?? "archived_desc") as ArchiveSortOption)
            }
            data={archiveSortOptions}
          />
        </SimpleGrid>
      </Paper>
      {rows.length === 0 && (
        <Paper className="empty-state inline" withBorder radius="md" p="md">
          <Text c="dimmed">
            No archived tasks yet. Archive finished cards from the Done column
            of the board.
          </Text>
        </Paper>
      )}
      {rows.length > 0 && visibleRows.length === 0 && (
        <Paper className="empty-state inline" withBorder radius="md" p="md">
          <Text c="dimmed">No archived tasks match the search.</Text>
        </Paper>
      )}
      {visibleRows.map((row) => (
        <Card
          className="item task-list-card archive-card"
          withBorder
          shadow="xs"
          radius="md"
          padding={0}
          key={row.cardId}
        >
          <Box
            className="task-card-edit-area"
            role="button"
            tabIndex={0}
            aria-label={`Open ${row.taskId || row.title}`}
            onClick={() => onOpen(row)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpen(row);
              }
            }}
          >
            <Stack gap={6}>
              <Group className="task-card-title-row" gap="xs" wrap="nowrap">
                {row.taskId && (
                  <Badge variant="light" color="gray" size="sm">
                    {row.taskId}
                  </Badge>
                )}
                <Title order={3}>{row.title}</Title>
              </Group>
              {(row.labels.length > 0 || row.branchName) && (
                <Group gap={6}>
                  {row.labels.map((label) => (
                    <Badge
                      className="label-badge"
                      key={label}
                      variant="outline"
                      color="gray"
                      size="sm"
                    >
                      {label}
                    </Badge>
                  ))}
                  {row.branchName && (
                    <Badge
                      leftSection={<GitBranch size={12} aria-hidden />}
                      variant="light"
                      color="teal"
                      size="sm"
                    >
                      {row.branchName}
                    </Badge>
                  )}
                </Group>
              )}
              <Text size="xs" c="dimmed">
                Archived {formatArchivedAt(row.archivedAt)}
              </Text>
            </Stack>
          </Box>
          <Box className="task-ready-control">
            <Button
              type="button"
              variant="light"
              size="xs"
              leftSection={<ArchiveRestore size={14} />}
              loading={restoringId === row.cardId}
              aria-label={`Unarchive ${row.taskId || row.title}`}
              onClick={() => void unarchive(row)}
            >
              Unarchive
            </Button>
          </Box>
        </Card>
      ))}
    </Stack>
  );
};
