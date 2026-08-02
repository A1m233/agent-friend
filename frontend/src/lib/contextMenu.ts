import { isTauri } from "@/utils/tauri";

interface ContextMenuEventTarget {
  addEventListener(type: "contextmenu", listener: (event: Event) => void): void;
  removeEventListener(type: "contextmenu", listener: (event: Event) => void): void;
}

interface ContextMenuPolicyOptions {
  target?: ContextMenuEventTarget;
  enabled?: boolean;
}

interface ContextMenuPolicyNode {
  getAttribute(name: string): string | null;
  matches(selector: string): boolean;
}

interface TextSelection {
  toString(): string;
  containsNode(node: Node, allowPartialContainment?: boolean): boolean;
}

export type ProductContextMenuPolicy = "custom" | "native" | "suppress";

export const PRODUCT_CONTEXT_MENU_POLICY_ATTRIBUTE = "data-context-menu-policy";

const EDITABLE_CONTEXT_MENU_SELECTOR = [
  "textarea",
  "input:not([type])",
  'input[type="text"]',
  'input[type="search"]',
  'input[type="email"]',
  'input[type="url"]',
  'input[type="tel"]',
  'input[type="password"]',
  'input[type="number"]',
  '[contenteditable]:not([contenteditable="false"])',
  '[role="textbox"]',
].join(", ");

function isContextMenuPolicyNode(value: unknown): value is ContextMenuPolicyNode {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ContextMenuPolicyNode>;
  return typeof candidate.getAttribute === "function" && typeof candidate.matches === "function";
}

function isProductContextMenuPolicy(value: string | null): value is ProductContextMenuPolicy {
  return value === "custom" || value === "native" || value === "suppress";
}

/**
 * Check the complete composed path instead of only `event.target`.
 *
 * Web Component events are retargeted to the shadow host at the document boundary. Chat message
 * text lives inside TDesign's Shadow DOM, so checking only the host incorrectly treats a real text
 * selection as unrelated to the context-menu event.
 */
export function selectionIntersectsContextMenuPath(
  event: Event,
  selection: TextSelection | null,
): boolean {
  // WebView2 may retarget a real Shadow DOM selection's anchor/focus to the same host and report
  // `isCollapsed=true` even while `toString()` still contains the selected text. The text value is
  // therefore the reliable empty-selection signal at this boundary.
  if (!selection || !selection.toString()) return false;

  const path = typeof event.composedPath === "function" ? event.composedPath() : [event.target];
  return path.some((node) => {
    try {
      return selection.containsNode(node as Node, true);
    } catch {
      // `Window` may be present in the composed path but is not a DOM Node.
      return false;
    }
  });
}

function hasSelectedTextAtEventTarget(event: Event): boolean {
  if (typeof window === "undefined") return false;
  return selectionIntersectsContextMenuPath(event, window.getSelection());
}

/**
 * Resolve the target policy from the composed event path so editable content inside a Web Component
 * can still opt into the native edit menu through its host or an ancestor wrapper.
 */
export function resolveProductContextMenuPolicy(
  event: Event,
  hasSelectedText = hasSelectedTextAtEventTarget(event),
): ProductContextMenuPolicy {
  const path = typeof event.composedPath === "function" ? event.composedPath() : [event.target];
  const nodes = path.reduce<ContextMenuPolicyNode[]>((result, node) => {
    if (isContextMenuPolicyNode(node)) result.push(node);
    return result;
  }, []);

  for (const node of nodes) {
    const explicitPolicy = node.getAttribute(PRODUCT_CONTEXT_MENU_POLICY_ATTRIBUTE);
    if (isProductContextMenuPolicy(explicitPolicy)) return explicitPolicy;
  }

  return hasSelectedText || nodes.some((node) => node.matches(EDITABLE_CONTEXT_MENU_SELECTOR))
    ? "native"
    : "suppress";
}

/**
 * 安装产品窗口统一右键策略。
 *
 * Tauri 中默认屏蔽产品外壳的 WebView 原生菜单；输入、文本编辑区、当前选中文本以及
 * 显式标记为 `data-context-menu-policy="native"` 的区域暂时保留系统编辑菜单。自定义
 * ContextMenu 区域使用 `custom`，由对应 UI primitive 呈现产品菜单。浏览器开发态仍
 * 保留 Inspect。
 */
export function installProductContextMenuPolicy({
  target = document,
  enabled = isTauri(),
}: ContextMenuPolicyOptions = {}): () => void {
  if (!enabled) return () => {};

  const applyContextMenuPolicy = (event: Event) => {
    if (resolveProductContextMenuPolicy(event) !== "native") event.preventDefault();
  };
  target.addEventListener("contextmenu", applyContextMenuPolicy);

  return () => target.removeEventListener("contextmenu", applyContextMenuPolicy);
}
