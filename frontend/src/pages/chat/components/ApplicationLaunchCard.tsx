import { StatusDot, type StatusDotProps } from "@/components/ui";
import { useCommandRuntimeStore } from "@/stores/commandRuntime";
import type { ToolBlock } from "@/types/chat";
import { cn } from "@/utils/cn";

function object(raw?: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(raw || "{}");
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function ApplicationLaunchCard({ block, embedded, isPrimaryActivity }: {
  block: ToolBlock;
  embedded?: boolean;
  isPrimaryActivity?: boolean;
}) {
  const approval = useCommandRuntimeStore((state) => Object.values(state.approvals)
    .find((request) => request.kind === "desktop_application" && request.tool_call_id === block.toolCallId));
  const args = object(block.args);
  const result = object(block.result);
  const status = result.status ?? (block.status === "running" ? approval?.status : "unknown");
  const label = status === "accepted" ? "已提交打开请求"
    : status === "unknown" || status === "dispatching" || status === "prepared" ? "启动结果未知"
      : status === "denied" ? "已拒绝"
        : status === "cancelled" ? "已取消"
          : status === "pending" ? "等待授权"
            : block.status === "running" ? "正在打开" : "未能打开";
  const tone: StatusDotProps["tone"] = status === "accepted" ? "success"
    : status === "failed" ? "danger" : "warning";
  return (
    <div className={cn(embedded ? "w-full max-w-none" : "max-w-[85%]",
      "rounded-lg border border-border bg-surface/60 px-3 py-2 text-xs")}>
      <div className="flex items-center gap-2">
        <StatusDot tone={tone} pulse={isPrimaryActivity !== false && block.status === "running"} />
        <span className="font-medium text-fg">打开桌面应用</span>
        <span className="ml-auto text-muted">{label}</span>
      </div>
      {typeof args.executable_path === "string" && <p className="mt-2 break-all text-fg">{args.executable_path}</p>}
      <p className="mt-2 text-muted">应用由桌面管理；任务结束或取消后仍会保留。</p>
      {status === "accepted" ? <p className="mt-1 text-muted">窗口及应用是否就绪仍需检查。</p>
        : status === "unknown" || status === "dispatching" || status === "prepared"
          ? <p className="mt-1 text-warning">应用可能已打开。请先检查已有窗口，不要直接重复启动。</p>
          : typeof result.message === "string" && <p className="mt-1 text-muted">{result.message}</p>}
      {status === "pending" && <p className="mt-1 text-muted">请在输入框上方处理当前授权。</p>}
    </div>
  );
}
