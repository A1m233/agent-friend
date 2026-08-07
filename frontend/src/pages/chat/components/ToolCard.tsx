import { cn } from "@/utils/cn";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  StatusDot,
  type StatusDotProps,
} from "@/components/ui";
import type { ToolBlock, ToolStatus } from "@/types/chat";
import { SocialAuthCard } from "./SocialAuthCard";

/**
 * 工具调用卡片（自写，AC-M3.2）。
 *
 * 为什么不用 tdesign：`@tdesign-react/chat`（alpha web-components）的 `<ChatMessage>`
 * 只可靠渲染 markdown，喂给它的 `toolcall` 块不出卡片（见 010 design §4.5 降级预案）。
 * 故工具调用过程态（进行中 → 完成 / 失败）由本组件自渲染，文本仍交回 tdesign。
 */

const STATUS_META: Record<
  ToolStatus,
  { label: string; tone: StatusDotProps["tone"]; pulse?: boolean; text: string }
> = {
  running: { label: "调用中", tone: "accent", pulse: true, text: "text-accent" },
  done: { label: "已完成", tone: "success", text: "text-muted" },
  error: { label: "失败", tone: "danger", text: "text-danger" },
};

function prettyArgs(args: string): string {
  if (!args) return "";
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}

interface SkillCardDetails {
  name: string;
  source: string;
  reason: string;
}

function skillCardDetails(block: ToolBlock): SkillCardDetails {
  let id = "";
  let name = "";
  let source = "";
  let reason = "";
  try {
    const args = JSON.parse(block.args) as Record<string, unknown>;
    id = typeof args.id === "string" ? args.id : "";
    name = typeof args.name === "string" ? args.name : "";
    source = typeof args.source === "string" ? args.source : "";
    reason = typeof args.reason === "string" ? args.reason : "";
  } catch {
    // The tool card remains useful even when a provider emitted malformed args.
  }
  if (!name && block.result) {
    name = block.result.match(/<name>([^<]+)<\/name>/)?.[1] ?? "";
  }
  if (!source && block.result) {
    source = block.result.match(/<source>([^<]+)<\/source>/)?.[1] ?? "";
  }
  return { name: name || id || "未知 Skill", source, reason };
}

function SkillStatusCard({ block }: { block: ToolBlock }) {
  const baseMeta = STATUS_META[block.status];
  const meta = block.status === "running"
    ? { ...baseMeta, label: "加载中" }
    : block.status === "done"
      ? { ...baseMeta, label: "已加载" }
      : { ...baseMeta, label: "加载失败" };
  const details = skillCardDetails(block);

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={meta.tone} pulse={meta.pulse} />
        <span className="font-medium text-fg">Skill · {details.name}</span>
        <span className={cn("ml-auto", meta.text)}>{meta.label}</span>
      </div>
      {(details.reason || details.source || block.status === "error") && (
        <div className="mt-1.5 space-y-1 text-muted">
          {details.reason && <p>{details.reason}</p>}
          {details.source && <p>来源：{details.source}</p>}
          {block.status === "error" && block.result && <p className="text-danger">{block.result}</p>}
        </div>
      )}
    </div>
  );
}

export function ToolCard({ block }: { block: ToolBlock }) {
  if (block.name === "request_social_auth" && block.status === "done") {
    return <SocialAuthCard block={block} />;
  }
  if (block.name === "skill_read") {
    return <SkillStatusCard block={block} />;
  }
  const meta = STATUS_META[block.status];
  const args = prettyArgs(block.args);
  const hasDetails = Boolean(args || block.result);

  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusDot tone={meta.tone} pulse={meta.pulse} />
        <span className="font-medium text-fg">{block.name}</span>
        <span className={cn("ml-auto", meta.text)}>{meta.label}</span>
      </div>
      {hasDetails && (
        <Collapsible className="mt-1.5">
          <CollapsibleTrigger className="cursor-pointer select-none text-muted transition-colors hover:text-fg">
            参数 / 结果
          </CollapsibleTrigger>
          <CollapsibleContent>
            {args && (
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-bg/60 p-2 text-xs text-fg">
                {args}
              </pre>
            )}
            {block.result && (
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-bg/60 p-2 text-xs text-fg">
                {block.result}
              </pre>
            )}
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}
