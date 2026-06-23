import { Moon, Sun } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui";
import { useTheme, type Theme } from "@/hooks/useTheme";

/**
 * 019 · 设置窗口。
 *
 * 027 扩展：加入 light/dark 主题切换，用 shadcn Tabs 组件 + 太阳/月亮图标。
 * 状态持久化到 localStorage，由 useTheme hook 管理。
 */
export function SettingsApp() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-6 bg-bg p-6 text-fg">
      <h1 className="text-xl font-semibold">设置</h1>

      <div className="flex w-full max-w-xs flex-col gap-3 rounded-lg border border-border bg-surface/50 p-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">主题</span>
          <Tabs
            value={theme}
            onValueChange={(value) => setTheme(value as Theme)}
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
        <p className="text-xs text-muted">
          切换后会保存到本地，刷新页面仍然生效。
        </p>
      </div>
    </div>
  );
}
