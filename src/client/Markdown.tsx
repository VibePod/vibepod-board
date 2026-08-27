import { Anchor, Code } from "@mantine/core";
import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// react-markdown ignores raw HTML unless rehype-raw is added, so task content
// stays safe to render without extra sanitizing.
const sharedComponents: Components = {
  a: ({ children, href }) => (
    <Anchor href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </Anchor>
  ),
  code: ({ children, className }) =>
    className?.startsWith("language-") ? (
      <code className={className}>{children}</code>
    ) : (
      <Code>{children}</Code>
    ),
};

const inlineComponents: Components = {
  ...sharedComponents,
  p: ({ children }) => <>{children}</>,
};

export const MarkdownText = ({ children }: { children: string }): ReactNode => (
  <div className="markdown-body">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={sharedComponents}>
      {children}
    </ReactMarkdown>
  </div>
);

/**
 * Renders a single line of markdown without the wrapping paragraph, for use
 * inside list items and other inline contexts.
 */
export const InlineMarkdown = ({
  children,
}: {
  children: string;
}): ReactNode => (
  <span className="markdown-body inline">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={inlineComponents}>
      {children}
    </ReactMarkdown>
  </span>
);
