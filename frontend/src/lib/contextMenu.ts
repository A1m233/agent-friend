import { isTauri } from "@/utils/tauri";

interface ContextMenuEventTarget {
  addEventListener(type: "contextmenu", listener: (event: Event) => void): void;
  removeEventListener(type: "contextmenu", listener: (event: Event) => void): void;
}

interface ContextMenuSuppressionOptions {
  target?: ContextMenuEventTarget;
  enabled?: boolean;
}

/**
 * 屏蔽产品浮窗中的浏览器原生右键菜单。
 *
 * 只在 Tauri 运行态默认启用，浏览器调试仍保留 Inspect 等开发能力。调用方应限制在
 * 不承载文本编辑/复制语义的产品窗口；chat、settings 等输入型窗口不得接入。
 */
export function installProductContextMenuSuppression({
  target = document,
  enabled = isTauri(),
}: ContextMenuSuppressionOptions = {}): () => void {
  if (!enabled) return () => {};

  const preventNativeContextMenu = (event: Event) => event.preventDefault();
  target.addEventListener("contextmenu", preventNativeContextMenu);

  return () => target.removeEventListener("contextmenu", preventNativeContextMenu);
}
