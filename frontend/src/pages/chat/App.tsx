import { useEffect, type CSSProperties } from "react";
import { SidebarProvider, StatusDot, type StatusDotProps } from "@/components/ui";
import { useConnectionStore, type ConnectionStatus } from "@/stores";
import { HistorySidebar } from "./components/HistorySidebar";
import { MessageList } from "./components/MessageList";
import { Composer } from "./components/Composer";

/**
 * 对话窗（常规不透明）。M10.3：接 agent-bridge 跑通流式对话。
 * 左侧历史会话，右侧 header（连接态）+ 消息列表 + 输入条。
 * 注：persona 选择/切换暂不在前端呈现——待前端补齐人格增删改查后再接入。
 */
export function ChatApp() {
  const status = useConnectionStore((s) => s.status);
  const ping = useConnectionStore((s) => s.ping);

  useEffect(() => {
    void ping();
    // 18 顺手修：dev cold start 时 bridge Python 启动慢于 chat 窗 mount，原 mount-only ping
    // 失败后永远不重试 → "未连接"卡死。改为每 3s 轮询：dev 启动 bridge 慢 / 偶发断连都
    // 自愈；bridge healthz 成本极小，online 时也跑作为健康检查（bridge 中途挂自动转 offline）。
    const id = setInterval(() => {
      void ping();
    }, 3000);
    return () => clearInterval(id);
  }, [ping]);

  return (
    <SidebarProvider
      className="h-screen bg-bg text-fg"
      style={{ "--sidebar-width": "15rem" } as CSSProperties}
    >
      <HistorySidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end border-b border-border px-4 py-2.5">
          <ConnectionDot status={status} />
        </header>
        <MessageList />
        <Composer />
      </div>
    </SidebarProvider>
  );
}

const CONNECTION_TONE: Record<ConnectionStatus, StatusDotProps["tone"]> = {
  online: "success",
  offline: "danger",
  unknown: "warning",
};

function ConnectionDot({ status }: { status: ConnectionStatus }) {
  const label = status === "online" ? "已连接" : status === "offline" ? "未连接" : "连接中";
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <StatusDot tone={CONNECTION_TONE[status]} size="md" />
      {label}
    </span>
  );
}
