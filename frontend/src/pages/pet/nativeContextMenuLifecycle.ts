interface NativeContextMenuPopupOptions {
  popup: () => Promise<void>;
  onOpenChange: (open: boolean) => void;
}

/**
 * 将原生菜单的阻塞式 popup 生命周期投影为 React 可观察状态。
 *
 * Tauri 的 popup Promise 会在系统菜单结束后返回；finally 确保菜单创建或弹出失败时
 * ActionBar 也不会残留在保活状态。
 */
export async function runNativeContextMenuPopup({
  popup,
  onOpenChange,
}: NativeContextMenuPopupOptions): Promise<void> {
  onOpenChange(true);
  try {
    await popup();
  } finally {
    onOpenChange(false);
  }
}
