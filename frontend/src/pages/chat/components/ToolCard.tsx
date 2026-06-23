import { cn } from "@/utils/cn";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  StatusDot,
  type StatusDotProps,
} from "@/components/ui";
import type { ToolBlock, ToolStatus } from "@/types/chat";

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

export function ToolCard({ block }: { block: ToolBlock }) {
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
