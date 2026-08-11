import { Group, Paper, SegmentedControl, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { InlineMarkdown, MarkdownText } from "./Markdown.js";
import {
  criteriaPreviewItems,
  hasMarkdownContent,
  type MarkdownFieldMode,
  normalizeMarkdownFieldMode,
} from "./markdownField.js";

type MarkdownFieldProps = {
  label: string;
  mode: MarkdownFieldMode;
  onModeChange: (mode: MarkdownFieldMode) => void;
  children: ReactNode;
  preview: ReactNode;
};

export const MarkdownField = ({
  label,
  mode,
  onModeChange,
  children,
  preview,
}: MarkdownFieldProps): ReactNode => (
  <Stack gap={6}>
    <Group justify="space-between" align="center" gap="sm">
      <Text size="sm" fw={700} className="markdown-field-label">
        {label}
      </Text>
      <SegmentedControl
        size="xs"
        aria-label={`${label} view`}
        value={mode}
        onChange={(value) => onModeChange(normalizeMarkdownFieldMode(value))}
        data={[
          { value: "edit", label: "Edit" },
          { value: "preview", label: "Preview" },
        ]}
      />
    </Group>
    {mode === "edit" ? (
      children
    ) : (
      <Paper className="markdown-preview" withBorder radius="sm" p="md">
        {preview}
      </Paper>
    )}
  </Stack>
);

export const MarkdownPreview = ({
  value,
  emptyText,
}: {
  value: string;
  emptyText: string;
}): ReactNode =>
  hasMarkdownContent(value) ? (
    <MarkdownText>{value}</MarkdownText>
  ) : (
    <Text c="dimmed">{emptyText}</Text>
  );

export const CriteriaPreview = ({
  value,
  emptyText,
}: {
  value: string;
  emptyText: string;
}): ReactNode => {
  const items = criteriaPreviewItems(value);
  if (items.length === 0) {
    return <Text c="dimmed">{emptyText}</Text>;
  }
  return (
    <ul className="overview-criteria-list">
      {items.map((item) => (
        <li key={item}>
          <InlineMarkdown>{item}</InlineMarkdown>
        </li>
      ))}
    </ul>
  );
};
