import { useEffect } from "react";
import { PetBubble } from "@/components/pet/PetBubble";
import { startPetBubbleSubscriber, attachBubbleWindowSync } from "@/stores/petBubble";

/**
 * 016 · 桌宠气泡独立窗（label="bubble"）入口。
 *
 * 承载层：015 的 PetBubble 组件 + petBubble store + push subscriber 全部搬到这个
 * 独立 webview。pet 主窗回 240×320 只承载形象（见 pet/App.tsx）；bubble window 由
 * Rust 侧 `bubble_window.rs` 控制显隐 / size / 位置（16ms tick + outer_position 跟随）。
 *
 * 本组件职责（M16.6）：
 * - `startPetBubbleSubscriber()` —— tauri event `agent://push` → `store.ingest`
 *   （015 已有逻辑，搬位置不改实现）
 * - `attachBubbleWindowSync()` —— phase 变化 → invoke `show_bubble` / `hide_bubble`，
 *   让 Rust 侧 bubble window 显隐与 store 状态严格同步
 * - 渲染 `<PetBubble />` —— 015 已有组件，M16.6 改造去掉了窗内 absolute 定位
 *
 * 透明背景：bubble webview 整窗 transparent，CSS root 也透明 —— 让 bubble 实心区
 * 由 PetBubble 自己用 `bg-surface` 等语义 token 上色，气泡之外的窗体保持透明，
 * 视觉上只看到一张气泡。
 */
export function BubbleApp() {
  useEffect(() => {
    const unsubSync = attachBubbleWindowSync();
    let unlistenPush: (() => void) | null = null;
    void startPetBubbleSubscriber().then((u) => {
      unlistenPush = u;
    });
    return () => {
      unsubSync();
      unlistenPush?.();
    };
  }, []);

  return (
    <div className="flex h-full w-full items-start justify-center bg-transparent p-1">
      <PetBubble />
    </div>
  );
}
