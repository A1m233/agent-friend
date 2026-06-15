import { useEffect } from "react";
import {
  Button,
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui";
import { useConnectionStore, useConversationStore, useSessionsStore } from "@/stores";

/**
 * 历史会话侧栏：列表展示 + 切换（R-M3.4）。bridge 为真相源，列表来自 sessions store。
 * 用 shadcn Sidebar（collapsible="none" 固定面板）；会话项 = SidebarMenuButton（isActive 高亮）。
 * 外层需由 ChatApp 套 SidebarProvider。
 *
 * **18 改连接状态驱动 refresh**：原 mount-only refresh 在 dev cold start 时 bridge 还没
 * ready → fetch 失败 list 留空；之后 bridge 起来也不会重拉。改为 status === "online" 时
 * refresh，让连接 unknown→online / offline→online 转换都自动重拉历史列表。
 */
export function HistorySidebar() {
  const list = useSessionsStore((s) => s.list);
  const refresh = useSessionsStore((s) => s.refresh);
  const status = useConnectionStore((s) => s.status);
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const openSession = useConversationStore((s) => s.openSession);
  const newSession = useConversationStore((s) => s.newSession);

  useEffect(() => {
    if (status === "online") {
      void refresh();
    }
  }, [status, refresh]);

  return (
    <Sidebar collapsible="none" className="border-r border-border">
      <SidebarHeader>
        <Button className="w-full" onClick={() => newSession()}>
          + 新对话
        </Button>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          {list.length === 0 ? (
            <p className="px-2 py-4 text-center text-xs text-muted">还没有历史会话</p>
          ) : (
            <SidebarMenu>
              {list.map((s) => (
                <SidebarMenuItem key={s.session_id}>
                  <SidebarMenuButton
                    isActive={s.session_id === currentSessionId}
                    onClick={() => void openSession(s.session_id)}
                  >
                    <span>{s.title || "新对话"}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          )}
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
