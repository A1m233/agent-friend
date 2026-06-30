import { useState, type CSSProperties } from "react";
import { Moon, Sun } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
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
import { AvatarModelPanel } from "./components/AvatarModelPanel";
import { RuntimeConfigPanel } from "./components/RuntimeConfigPanel";
import { SettingsGroup } from "./components/SettingsGroup";
import { SettingsPageLayout } from "./components/SettingsPageLayout";
import { SettingsRow } from "./components/SettingsRow";

/**
 * 028 · 设置中心：左侧分类导航 + 右侧滚动内容的两栏 shell。
 *
 * 后续新增设置项按 design.md 扩展点接入。
 */
type SettingsSection = "general" | "runtime" | "voice" | "pet";

export function SettingsApp() {
  const [section, setSection] = useState<SettingsSection>("general");
  const [theme, setTheme] = useSetting("theme");
  const [voiceTunnelConsentAccepted, setVoiceTunnelConsentAccepted] = useSetting(
    "voiceTunnelConsentAccepted",
  );

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
                    isActive={section === "general"}
                    onClick={() => setSection("general")}
                  >
                    通用
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "voice"}
                    onClick={() => setSection("voice")}
                  >
                    语音
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "pet"}
                    onClick={() => setSection("pet")}
                  >
                    桌宠
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={section === "runtime"}
                    onClick={() => setSection("runtime")}
                  >
                    模型与凭据
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>

        <main className="flex min-w-0 flex-1 flex-col bg-bg">
          {section === "general" ? (
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
            </SettingsPageLayout>
          ) : section === "runtime" ? (
            <RuntimeConfigPanel />
          ) : section === "pet" ? (
            <AvatarModelPanel />
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
