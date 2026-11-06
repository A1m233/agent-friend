import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Moon, Sun } from "lucide-react";
import { FeedbackIcon } from "@/components/app/FeedbackIcon";
import { PlatformAccountsPanel } from "./components/PlatformAccountsPanel";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
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
  DEFAULT_SETTINGS_TARGET,
  locateSettings,
  PRIMARY_SETTINGS_SECTIONS,
  type SettingsOpenRequest,
  type SettingsTarget,
} from "@/lib/settings/navigation";
import type { WorkspaceNavigationEnvelope } from "@/lib/workspace/navigation";
import { AvatarModelPanel } from "./components/AvatarModelPanel";
import { ArchivedSessionsPanel } from "./components/ArchivedSessionsPanel";
import { RuntimeConfigPanel } from "./components/RuntimeConfigPanel";
import { PersonaPanel } from "./components/PersonaPanel";
import { FeedbackDiagnosticsPanel } from "./components/FeedbackDiagnosticsPanel";
import { SettingsGroup } from "./components/SettingsGroup";
import { SettingsPageLayout } from "./components/SettingsPageLayout";
import { SettingsRow } from "./components/SettingsRow";
import { AutostartSetting } from "./components/AutostartSetting";
import { BackgroundOnlineSetting } from "./components/BackgroundOnlineSetting";
import { AuthorizationSetting } from "./components/AuthorizationSetting";
import { CompanionshipSetting } from "./components/CompanionshipSetting";
import { ReminderNotificationSetting } from "./components/ReminderNotificationSetting";
import { NotificationSettings } from "./components/NotificationSettings";
import { PermissionPanel } from "./permissions/PermissionPanel";
import { DataMigrationPanel } from "./data-migration/DataMigrationPanel";
import type { RuntimeConfigGroup } from "@/lib/runtime-config";

/**
 * 028 · 设置中心：左侧分类导航 + 右侧滚动内容的两栏 shell。
 *
 * 后续新增设置项按 design.md 扩展点接入。
 */
export function SettingsApp({
  navigation,
}: {
  navigation?: WorkspaceNavigationEnvelope | null;
}) {
  const [section, setSection] = useState<SettingsTarget>(DEFAULT_SETTINGS_TARGET);
  const [feedbackPrefill, setFeedbackPrefill] = useState<{
    requestId: number;
    sessionId?: string;
  } | null>(null);
  const [runtimeGroup, setRuntimeGroup] = useState<RuntimeConfigGroup | undefined>();
  const lastNavigationRequestId = useRef(0);
  const [replyStyle, setReplyStyle] = useSetting("imReplyStyle");
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
      setRuntimeGroup(location.runtimeGroupId);
      setFeedbackPrefill(
        location.section === "feedback" && requestId
          ? { requestId, sessionId: location.preselectSessionId }
          : null,
      );
    },
    [],
  );

  useEffect(() => {
    if (navigation?.target !== "settings" || !navigation.settings) return;
    navigate(navigation.settings, navigation.requestId);
  }, [navigate, navigation]);

  const selectSection = (target: SettingsTarget) => navigate({ target });

  return (
    <TooltipProvider delayDuration={0}>
      <SidebarProvider
        className="h-full bg-bg text-fg"
        style={{ "--sidebar-width": "200px" } as CSSProperties}
      >
        <Sidebar collapsible="none" className="border-r border-border">
          <SidebarHeader>
            <h1 className="px-2 pt-2 text-lg font-semibold text-fg">设置</h1>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarMenu>
                {PRIMARY_SETTINGS_SECTIONS.map(({ key, label }) => (
                  <SidebarMenuItem key={key}>
                    <SidebarMenuButton
                      isActive={section === key}
                      onClick={() => selectSection(key)}
                    >
                      {label}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
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
                  <FeedbackIcon />
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
              <SettingsGroup
                title="后台运行"
                description="屏幕熄灭不等于电脑休眠。屏幕关闭后，Agent 和 QQ 仍可继续运行；电脑进入休眠后，Agent 和 QQ 会暂停运行。合盖是否休眠由系统设置决定（大多数笔记本默认合盖即休眠），agent-friend 不会自动修改合盖行为。"
              >
                <BackgroundOnlineSetting />
              </SettingsGroup>
            </SettingsPageLayout>
          ) : section === "chat" ? (
            <SettingsPageLayout title="对话">
              <SettingsGroup title="即时消息" description="用于普通 QQ 回复和 AF 主动发来的消息。自然聊天会口语化表达并按需分条，原样发送保留会话原文。">
                <SettingsRow label="IM 回复风格">
                  <Select value={replyStyle} onValueChange={(value) => {
                    if (value === "natural" || value === "original") void setReplyStyle(value);
                  }}>
                    <SelectTrigger aria-label="IM 回复风格" className="w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="natural">自然聊天</SelectItem>
                      <SelectItem value="original">原样发送</SelectItem>
                    </SelectContent>
                  </Select>
                </SettingsRow>
              </SettingsGroup>
            </SettingsPageLayout>
          ) : section === "companionship" ? (
            <SettingsPageLayout title="自主陪伴">
              <SettingsGroup title="活动与联系"><CompanionshipSetting /></SettingsGroup>
              <SettingsGroup title="授权"><AuthorizationSetting /></SettingsGroup>
              <PlatformAccountsPanel />
            </SettingsPageLayout>
          ) : section === "notifications" ? (
            <SettingsPageLayout title="通知">
              <SettingsGroup title="桌宠通知">
                <NotificationSettings />
              </SettingsGroup>
              <SettingsGroup title="待办提醒">
                <ReminderNotificationSetting />
              </SettingsGroup>
            </SettingsPageLayout>
          ) : section === "archived" ? (
            <ArchivedSessionsPanel />
          ) : section === "runtime" ? (
            <RuntimeConfigPanel focusGroup={runtimeGroup} />
          ) : section === "pet" ? (
            <AvatarModelPanel />
          ) : section === "feedback" ? (
            <FeedbackDiagnosticsPanel prefill={feedbackPrefill} />
          ) : section === "permissions" ? (
            <PermissionPanel />
          ) : section === "migration" ? (
            <DataMigrationPanel />
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
