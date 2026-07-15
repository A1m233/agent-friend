import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const PERSONA_MARKDOWN_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mb-4 mt-6 text-xl font-semibold leading-tight first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-6 text-base font-semibold leading-snug first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-2 mt-5 text-sm font-semibold leading-snug first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-border pl-4 text-muted">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-5 border-border" />,
  table: ({ children }) => (
    <table className="my-3 w-full border-collapse text-left text-sm">{children}</table>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-surface px-3 py-2 font-medium">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-border px-3 py-2 align-top">{children}</td>
  ),
};

export function PersonaMarkdown({ content }: { content: string }) {
  return (
    <div className="min-h-48 text-sm leading-6">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={PERSONA_MARKDOWN_COMPONENTS}
        skipHtml
      >
        {content}
      </Markdown>
    </div>
  );
}
