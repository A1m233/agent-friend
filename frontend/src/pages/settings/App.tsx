import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Moon, Sun } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarFooter,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  Switch,
  Tabs,
  TabsList,
  TabsTrigger,
  TooltipProvider,
} from "@/components/ui";
import { useSetting, type ThemeMode } from "@/lib/settings";
import {
  consumePendingSettingsNavigation,
  listenSettingsNavigation,
  locateSettings,
  type SettingsNavigationEnvelope,
  type SettingsOpenRequest,
  type SettingsTarget,
} from "@/lib/settings/navigation";
import { AvatarModelPanel } from "./components/AvatarModelPanel";
import { ArchivedSessionsPanel } from "./components/ArchivedSessionsPanel";
import { RuntimeConfigPanel } from "./components/RuntimeConfigPanel";
import { PersonaPanel } from "./components/PersonaPanel";
import { FeedbackDiagnosticsPanel } from "./components/FeedbackDiagnosticsPanel";
import { SettingsGroup } from "./components/SettingsGroup";
import { SettingsPageLayout } from "./components/SettingsPageLayout";
import { SettingsRow } from "./components/SettingsRow";
import { AutostartSetting } from "./components/AutostartSetting";
import { PermissionPanel } from "./permissions/PermissionPanel";

/**
 * 028 · 设置中心：左侧分类导航 + 右侧滚动内容的两栏 shell。
 *
 * 后续新增设置项按 design.md 扩展点接入。
 */
export function SettingsApp() {
  const [section, setSection] = useState<SettingsTarget>("general");
  const [feedbackPrefill, setFeedbackPrefill] = useState<{
    requestId: number;
    sessionId?: string;
  } | null>(null);
  const lastNavigationRequestId = useRef(0);
  const [theme, setTheme] = useSetting("theme");
  const [voiceTunnelConsentAccepted, setVoiceTunnelConsentAccepted] = useSetting(
    "voiceTunnelConsentAccepted",
  );

  const navigate = useCallback(
    (request: SettingsOpenRequest, requestId?: number) => {
      if (requestId && requestId <= lastNavigationRequestId.current) return;
      if (requestId) lastNavigationRequestId.current = requestId;
      const location = locateSettings(request);
      setSection(location.section);
      setFeedbackPrefill(
        location.section === "feedback" && requestId
          ? { requestId, sessionId: location.preselectSessionId }
          : null,
      );
    },
    [],
  );

  useEffect(() => {
    const applyEnvelope = (envelope: SettingsNavigationEnvelope) => {
      navigate(envelope.request, envelope.requestId);
    };
    void consumePendingSettingsNavigation().then((pending) => {
      if (pending) applyEnvelope(pending);
    });
    const unlistenPromise = listenSettingsNavigation(applyEnvelope);
    return () => {
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, [navigate]);

  const selectSection = (target: SettingsTarget) => navigate({ target });

  return (
    <TooltipProvider delayDuration={0}>
      <SidebarProvider
        className="h-screen bg-bg text-fg"
        style={{ "--sidebar-width": "200px" } as CSSProperties}
      >
        <Sidebar collapsible="none" className="border-r border-border">
          <SidebarHeader>
            <h1 className="px-2 pt-2 text-lg font-semibold text-fg">设置</h1>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "persona"}
                    onClick={() => selectSection("persona")}
                  >
                    人格
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "permissions"}
                    onClick={() => selectSection("permissions")}
                  >
                    系统权限
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "general"}
                    onClick={() => selectSection("general")}
                  >
                    通用
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "pet"}
                    onClick={() => selectSection("pet")}
                  >
                    桌宠
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "voice"}
                    onClick={() => selectSection("voice")}
                  >
                    语音
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "runtime"}
                    onClick={() => selectSection("runtime")}
                  >
                    模型与凭据
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "archived"}
                    onClick={() => selectSection("archived")}
                  >
                    已归档对话
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
          <SidebarFooter>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={section === "feedback"}
                  onClick={() => selectSection("feedback")}
                >
                  帮助与反馈
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarFooter>
        </Sidebar>

        <main className="flex min-w-0 flex-1 flex-col bg-bg">
          <PersonaPanel active={section === "persona"} />
          {section === "persona" ? null : section === "general" ? (
            <SettingsPageLayout title="通用">
              <SettingsGroup title="外观">
                <SettingsRow label="主题">
                  <Tabs
                    value={theme}
                    onValueChange={(value) => void setTheme(value as ThemeMode)}
                  >
                    <TabsList>
                      <TabsTrigger value="light" aria-label="浅色主题">
                        <Sun className="size-4" />
                      </TabsTrigger>
                      <TabsTrigger value="dark" aria-label="深色主题">
                        <Moon className="size-4" />
                      </TabsTrigger>
                    </TabsList>
                  </Tabs>
                </SettingsRow>
              </SettingsGroup>
              <SettingsGroup title="启动">
                <AutostartSetting />
              </SettingsGroup>
            </SettingsPageLayout>
          ) : section === "archived" ? (
            <ArchivedSessionsPanel />
          ) : section === "runtime" ? (
            <RuntimeConfigPanel />
          ) : section === "pet" ? (
            <AvatarModelPanel />
          ) : section === "feedback" ? (
            <FeedbackDiagnosticsPanel prefill={feedbackPrefill} />
          ) : section === "permissions" ? (
            <PermissionPanel />
          ) : (
            <SettingsPageLayout title="语音">
              <SettingsGroup title="语音通话">
                <SettingsRow
                  label="同意进行公网穿透"
                  tooltip="未同意公网穿透前无法进行语音通话。agent-friend 不会保存火山凭证或公网 URL。"
                  tooltipAriaLabel="公网穿透说明"
                >
                  <Switch
                    checked={voiceTunnelConsentAccepted}
                    onCheckedChange={(checked) =>
                      void setVoiceTunnelConsentAccepted(checked)
                    }
                    aria-label="同意进行公网穿透"
                  />
                </SettingsRow>
              </SettingsGroup>
            </SettingsPageLayout>
          )}
        </main>
      </SidebarProvider>
    </TooltipProvider>
  );
}
