import { useState, type CSSProperties } from "react";
import { Info, Moon, Sun } from "lucide-react";
import {
  Button,
  ScrollArea,
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
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui";
import { useSetting, type ThemeMode } from "@/lib/settings";

/**
 * 028 · 设置中心：左侧分类导航 + 右侧滚动内容的两栏 shell。
 *
 * 目前只承载"通用 → 外观 → 主题"一项，后续新增设置项按 design.md 扩展点接入。
 */
type SettingsSection = "general" | "voice";

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
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>

        <main className="flex min-w-0 flex-1 flex-col bg-bg">
          <ScrollArea className="h-screen">
            <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
              {section === "general" ? (
                <>
                  <h2 className="text-xl font-semibold">通用</h2>

                  <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface/50 p-5">
                    <h3 className="text-sm font-medium text-muted">外观</h3>

                    <div className="flex items-center justify-between">
                      <span className="text-sm">主题</span>
                      <Tabs
                        value={theme}
                        onValueChange={(value) =>
                          void setTheme(value as ThemeMode)
                        }
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
                    </div>
                  </section>
                </>
              ) : (
                <>
                  <h2 className="text-xl font-semibold">语音</h2>

                  <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface/50 p-5">
                    <h3 className="text-sm font-medium text-muted">语音通话</h3>

                    <div className="flex items-center justify-between gap-4">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="text-sm">同意进行公网穿透</span>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              className="size-6 text-muted"
                              aria-label="公网穿透说明"
                            >
                              <Info className="size-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent className="max-w-xs" side="top">
                            未同意公网穿透前无法进行语音通话。agent-friend 不会保存火山凭证或公网 URL。
                          </TooltipContent>
                        </Tooltip>
                      </div>
                      <Switch
                        checked={voiceTunnelConsentAccepted}
                        onCheckedChange={(checked) =>
                          void setVoiceTunnelConsentAccepted(checked)
                        }
                        aria-label="同意进行公网穿透"
                      />
                    </div>
                  </section>
                </>
              )}
            </div>
          </ScrollArea>
        </main>
      </SidebarProvider>
    </TooltipProvider>
  );
}
