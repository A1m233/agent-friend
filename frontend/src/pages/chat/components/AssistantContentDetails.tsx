import { Button } from "@/components/ui";
import type { AssistantContentPart } from "@/types/chat";
import { classifyLink, managedLinkHref } from "@/lib/links/targets";

export function AssistantContentDetails({ parts = [] }: { parts?: AssistantContentPart[] }) {
  const refused = parts.some(part => part.kind === "refusal");
  const sources = parts.flatMap(part => part.annotations);
  if (!refused && !sources.length) return null;
  return <div className="mb-2 text-xs text-muted">
    {refused && <p>模型拒绝了这次请求</p>}
    {sources.length > 0 && <ul aria-label="回答来源" className="flex flex-wrap gap-x-3 gap-y-1">
      {sources.map((source, index) => {
        const target = typeof source.url === "string" ? classifyLink(source.url) : null;
        const label = source.title || source.filename || (target?.kind === "external" && /^https?:/i.test(target.value) ? new URL(target.value).hostname : "外部文件引用");
        return <li key={index}>{target && target.kind !== "unavailable"
          ? <Button asChild variant="link" size="sm" className="h-auto p-0 text-xs"><a href={managedLinkHref(target.value)}>{label}</a></Button>
          : <span>{label}（当前无法打开）</span>}</li>;
      })}
    </ul>}
  </div>;
}
