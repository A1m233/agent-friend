import {
  Button,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuSkeleton,
} from "@/components/ui";
import type { ConnectionStatus } from "@/stores";

/** Only used before the sidebar has any session rows to keep visible. */
export function HistorySidebarStatus({ loaded, loading, error, status, onRetry }: {
  loaded: boolean;
  loading: boolean;
  error: boolean;
  status: ConnectionStatus;
  onRetry: () => void;
}) {
  if (loaded) {
    return <p className="px-2 py-2 text-xs text-muted">还没有历史会话</p>;
  }

  if (status === "offline") {
    return <p role="status" className="px-2 py-2 text-xs text-muted">连接恢复后将加载会话</p>;
  }

  if (status === "online" && error && !loading) {
    return (
      <div role="alert" className="space-y-2 px-2 py-2">
        <p className="text-xs text-muted">会话列表加载失败</p>
        <Button size="sm" variant="outline" onClick={onRetry}>重试</Button>
      </div>
    );
  }

  // An online render can precede the effect that starts the first request.
  // Until a request succeeds, the initial empty array is not an empty history.
  return (
    <div role="status" aria-label="正在加载会话">
      <SidebarMenu aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <SidebarMenuItem key={index}>
            <SidebarMenuSkeleton className="*:bg-surface-hover" />
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </div>
  );
}
