/**
 * 019 · 设置窗口占位骨架。
 *
 * 本期范围（requirement.md §2 / §4.5）：仅起壳。不实现任何具体设置项 /
 * 设置项的存储 / 设置项的应用逻辑——那些后续单独立项往这个壳里填。
 *
 * 关闭行为由 Rust 侧 `on_window_event` 拦截 settings 窗 CloseRequested → 隐藏不销毁
 * （参 chat 窗约定）；再次点 ActionBar 设置按钮 invoke `open_settings` 显示同一实例。
 */
export function SettingsApp() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-2 bg-bg text-fg">
      <h1 className="text-xl font-semibold">设置</h1>
      <p className="text-sm text-muted-fg">设置项后续开放</p>
    </div>
  );
}
