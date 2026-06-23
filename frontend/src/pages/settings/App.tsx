import type { CSSProperties } from "react";
import { Moon, Sun } from "lucide-react";
import {
  ScrollArea,
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  Tabs,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { useSetting, type ThemeMode } from "@/lib/settings";

/**
 * 028 · 设置中心：左侧分类导航 + 右侧滚动内容的两栏 shell。
 *
 * 目前只承载"通用 → 外观 → 主题"一项，后续新增设置项按 design.md 扩展点接入。
 */
export function SettingsApp() {
  const [theme, setTheme] = useSetting("theme");

  return (
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
                <SidebarMenuButton isActive>通用</SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>

      <main className="flex min-w-0 flex-1 flex-col bg-bg">
        <ScrollArea className="h-screen">
          <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
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
          </div>
        </ScrollArea>
      </main>
    </SidebarProvider>
  );
}
