import type { CSSProperties } from "react";
import { Button } from "@/components/ui";

/**
 * 18 · dev-only 穿透切换按钮 —— 解决 inspector 打开后被 cursor passthrough
 * `setIgnoreCursorEvents(true)` 影响导致无法点击的体感。
 *
 * - 永远固定在屏幕左上角（`position: fixed`）
 * - `data-hit` 让 `usePetPassthrough` DOM 命中优先匹配此按钮，点击不被穿透吃掉
 * - `import.meta.env.DEV` gate，release build 整段被 tree-shake 掉，无运行期开销
 *
 * 使用方式：dev 调试期开 inspector 前点一下"穿透 OFF · 点开"，pet 窗变实窗，
 * 所有鼠标事件被 webview 接住 → inspector 可点。回到 product 体感测试时点回去。
 */

interface Props {
  disabled: boolean;
  onToggle: () => void;
}

const STYLE: CSSProperties = {
  position: "fixed",
  top: 8,
  left: 8,
  zIndex: 1000,
};

export function DevPassthroughToggle({ disabled, onToggle }: Props) {
  if (!import.meta.env.DEV) return null;
  return (
    <div data-hit style={STYLE}>
      <Button
        data-hit
        variant={disabled ? "outline" : "ghost"}
        size="pill"
        onClick={onToggle}
      >
        {disabled ? "🔓 穿透 OFF · 点开" : "🔒 穿透 ON · 点关"}
      </Button>
    </div>
  );
}
