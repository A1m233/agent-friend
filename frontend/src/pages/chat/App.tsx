import {
  Activity,
  lazy,
  Suspense,
  useEffect,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import { SidebarProvider, Spinner } from "@/components/ui";
import {
  startSessionSyncSubscriber,
  startCommandRuntimeSync,
  useCommandRuntimeStore,
  startFilesystemApprovalSync,
  startRuntimeCredentialSync,
  useFilesystemApprovalsStore,
  useConnectionStore,
  useConversationStore,
} from "@/stores";
import {
  publishActiveChatSession,
  subscribeActiveChatSessionRequests,
} from "@/lib/active-chat-session";
import { HistorySidebar } from "./components/HistorySidebar";
import { GlobalConversationSearchProvider } from "./components/GlobalConversationSearch";
import { useModelCatalogStore } from "@/lib/model-config";
import { startDesktopPresenceReporter } from "@/lib/desktop-presence";
import {
  startWorkspaceNavigationSync,
  useWorkspaceNavigationStore,
} from "@/lib/workspace/navigation";
import { ChatContentSurface } from "./components/ChatContentSurface";

const SettingsApp = lazy(() =>
  import("@/pages/settings/App").then((module) => ({ default: module.SettingsApp })),
);
const IMConnectPanel = lazy(() =>
  import("@/components/im/IMConnectPanel").then((module) => ({
    default: module.IMConnectPanel,
  })),
);
const MemoryInspectorApp = lazy(() =>
  import("@/pages/memory-inspector/App").then((module) => ({
    default: module.MemoryInspectorApp,
  })),
);

/**
 * 093 · 统一主窗口：左侧工作区导航 + 右侧平级内容 surface。
 */
export function ChatApp() {
  const status = useConnectionStore((s) => s.status);
  const ping = useConnectionStore((s) => s.ping);
  const currentSessionId = useConversationStore((s) => s.currentSessionId);
  const newSession = useConversationStore((s) => s.newSession);
  const loadModelCatalog = useModelCatalogStore((state) => state.load);
  const target = useWorkspaceNavigationStore((state) => state.target);
  const visitedTargets = useWorkspaceNavigationStore((state) => state.visitedTargets);
  const chatNavigation = useWorkspaceNavigationStore((state) => state.chatNavigation);
  const settingsNavigation = useWorkspaceNavigationStore(
    (state) => state.settingsNavigation,
  );
  const [memoryInspectorEnabled, setMemoryInspectorEnabled] = useState(import.meta.env.DEV);

  useEffect(() => {
    if (status === "online") void loadModelCatalog();
  }, [loadModelCatalog, status]);

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

  useEffect(() => startSessionSyncSubscriber(), []);
  useEffect(() => startRuntimeCredentialSync(), []);
  useEffect(() => startWorkspaceNavigationSync(), []);

  useEffect(() => {
    if (!chatNavigation?.chat?.newConversation) return;
    newSession();
  }, [chatNavigation, newSession]);

  useEffect(() => {
    void invoke<{ memoryInspector: boolean }>("get_local_capabilities")
      .then((capabilities) => setMemoryInspectorEnabled(capabilities.memoryInspector))
      .catch(() => setMemoryInspectorEnabled(false));
  }, []);

  useEffect(() => {
    if (status !== "online") {
      useFilesystemApprovalsStore.getState().markDisconnected();
      useCommandRuntimeStore.getState().markDisconnected();
      return;
    }
    const stopFilesystem = startFilesystemApprovalSync();
    const stopCommands = startCommandRuntimeSync();
    return () => {
      stopFilesystem();
      stopCommands();
    };
  }, [status]);

  useEffect(
    () =>
      subscribeActiveChatSessionRequests(
        () => useConversationStore.getState().currentSessionId,
      ),
    [],
  );

  useEffect(() => {
    void publishActiveChatSession(currentSessionId);
  }, [currentSessionId]);

  useEffect(
    () => startDesktopPresenceReporter(
      () => useConversationStore.getState().currentSessionId,
    ),
    [],
  );

  return (
    <SidebarProvider
      className="h-screen bg-bg text-fg"
      style={{ "--sidebar-width": "15rem" } as CSSProperties}
    >
      <GlobalConversationSearchProvider>
        <HistorySidebar memoryInspectorEnabled={memoryInspectorEnabled} />
        <div className="relative min-w-0 flex-1 overflow-hidden bg-bg text-fg">
          <WorkspaceSurface active={target === "chat"}>
            <ChatContentSurface />
          </WorkspaceSurface>
          {visitedTargets.includes("settings") ? (
            <WorkspaceSurface active={target === "settings"}>
              <SettingsApp navigation={settingsNavigation} />
            </WorkspaceSurface>
          ) : null}
          {visitedTargets.includes("im-connect") ? (
            <WorkspaceSurface active={target === "im-connect"}>
              <IMConnectPanel active={target === "im-connect"} />
            </WorkspaceSurface>
          ) : null}
          {memoryInspectorEnabled && visitedTargets.includes("memory-inspector") ? (
            <WorkspaceSurface active={target === "memory-inspector"}>
              <MemoryInspectorApp />
            </WorkspaceSurface>
          ) : null}
        </div>
      </GlobalConversationSearchProvider>
    </SidebarProvider>
  );
}

function WorkspaceSurface({
  active,
  children,
}: {
  active: boolean;
  children: ReactNode;
}) {
  return (
    <Activity mode={active ? "visible" : "hidden"}>
      <section className="absolute inset-0 min-h-0 min-w-0 overflow-hidden">
        <Suspense fallback={<WorkspaceSurfaceLoading />}>{children}</Suspense>
      </section>
    </Activity>
  );
}

function WorkspaceSurfaceLoading() {
  return (
    <div className="flex h-full items-center justify-center text-muted" role="status">
      <Spinner />
      <span className="ml-2 text-sm">正在打开...</span>
    </div>
  );
}
