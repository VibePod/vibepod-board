import { ActionIcon, Code, CopyButton, Group, Tooltip } from "@mantine/core";
import { Check, Copy } from "lucide-react";

/** Icon button that copies `value` and briefly confirms with "Copied". */
export const CopyValueButton = ({
  value,
  label,
}: {
  value: string;
  label: string;
}) => (
  <CopyButton value={value} timeout={1500}>
    {({ copied, copy }) => (
      <Tooltip label={copied ? "Copied" : `Copy ${label}`} withArrow>
        <ActionIcon
          className="copy-value-button"
          variant="subtle"
          color={copied ? "teal" : "gray"}
          aria-label={copied ? "Copied" : `Copy ${label}`}
          onClick={copy}
        >
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </ActionIcon>
      </Tooltip>
    )}
  </CopyButton>
);

/**
 * Code with a copy button. `pre` renders a `<pre><code>` block, `block` a Mantine block
 * `Code`, and `inline` an inline `Code` followed by the button.
 */
export const CopyableCode = ({
  value,
  label,
  variant = "pre",
}: {
  value: string;
  label: string;
  variant?: "pre" | "block" | "inline";
}) => {
  if (variant === "inline") {
    return (
      <Group className="copyable-inline" gap={4} wrap="nowrap">
        <Code>{value}</Code>
        <CopyValueButton value={value} label={label} />
      </Group>
    );
  }
  return (
    <div className="copyable-code">
      {variant === "block" ? (
        <Code block>{value}</Code>
      ) : (
        <pre>
          <code>{value}</code>
        </pre>
      )}
      <CopyValueButton value={value} label={label} />
    </div>
  );
};
