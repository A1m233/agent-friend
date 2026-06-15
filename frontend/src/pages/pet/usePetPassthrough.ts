import { useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import { slotBoundsHit } from "./slotBoundsHit";

/**
 * 透明窗鼠标穿透 hit-test（M10.2 spike → 17a 升级到 PIXI 整屏 overlay 形态）。
 *
 * 原理：Rust 用内置 cursor_position 以 ~60fps 把"光标相对 pet content 区的逻辑坐标"
 * 通过 `pet://cursor` 事件喂给前端；这里据此切 `setIgnoreCursorEvents`——空白透明区
 * 穿透到桌面，sprite / 操作栏可交互。
 *
 * 17a 升级（design §3.4 / §3.5）：
 *
 * 1. **isDragging 互锁**：drag 期间锁定 `setIgnoreCursorEvents(false)` 不再 toggle，
 *    防止快速划过空白区时 webview 失去 pointermove 导致 drag 中途断（design §3.5）
 * 2. **DOM data-hit 优先**：操作栏 DOM 在 PIXI canvas 之上，DOM elementFromPoint 仍能
 *    正确判定操作栏命中（不依赖 PIXI alpha 路径）
 * 3. **sprite 命中 = bounding box hit-test 兜底**（design §3.4）：PIXI alpha readPixels
 *    在 PIXI v8 部分 internal 收紧时不可达；本期落兜底 = avatar-slot getBounds() +
 *    point-in-rect。占位形象是圆形，bounding box 比 alpha 多命中约 21% 角区——
 *    17a 验收阶段可接受；17b 上 Live2D 时视情况升级 plumbing
 *
 * 为什么不能用浏览器 mousemove：一旦 setIgnoreCursorEvents(true)，webview 就收不到
 * 自己的鼠标事件，无法感知"光标再次移入"，必须靠 Rust 的全局光标喂给。
 */

interface Options {
  /** drag 期间锁定 `setIgnoreCursorEvents(false)`（design §3.5） */
  isDragging: boolean;
  /** sprite bounding box（CSS px，与 `pet://cursor` payload 同坐标系）；null 时不命中 sprite */
  spriteScreen: { x: number; y: number; w: number; h: number } | null;
  /**
   * AC-6 修复 · 驱动 ActionBar hover 显隐。
   *
   * 原方案是 PIXI slot.on("pointerover/out") → setHoverActionBar，但 cursor 离开
   * sprite 后 setIgnoreCursorEvents 切回 true、webview 不收事件 → PIXI pointerout 不触发
   * → hover state stuck 在 true。
   *
   * 改用本回调，由 Rust 60Hz cursor channel 驱动：cursor 在 sprite bounds 内 → true，
   * 在外 / 在 DOM data-hit 上 / drag 期间 → false。
   */
  setCursorOnSprite: (v: boolean) => void;
}

export function usePetPassthrough({ isDragging, spriteScreen, setCursorOnSprite }: Options) {
  useEffect(() => {
    if (!isTauri()) return; // 浏览器 web 调试下无桌面能力，跳过

    const win = getCurrentWindow();
    let ignored: boolean | null = null;
    let disposed = false;

    const apply = (shouldIgnore: boolean) => {
      if (shouldIgnore === ignored) return; // 只在变化时切，避免抖动
      ignored = shouldIgnore;
      void win.setIgnoreCursorEvents(shouldIgnore);
    };

    apply(true); // 初始默认穿透：空白处不挡桌面

    const unlisten = listen<{ x: number; y: number }>("pet://cursor", (e) => {
      if (disposed) return;
      // (1) drag 期间锁定 false；hover 状态从 cursorOnSprite 维度看是 "不算 hover"
      //     （但 ActionBar 显隐由 App.tsx 综合 isDragging || cursorOnSprite || hoverActionBarDom 决定）
      if (isDragging) {
        setCursorOnSprite(false);
        return apply(false);
      }
      // (2) DOM data-hit 优先（操作栏 DOM 在 canvas 之上）
      const el = document.elementFromPoint(e.payload.x, e.payload.y);
      if (el?.closest("[data-hit]")) {
        setCursorOnSprite(false); // cursor 在 ActionBar 上，不在 sprite 上
        return apply(false);
      }
      // (3) sprite bounding box 兜底（PIXI alpha 留 17b 升级）
      if (spriteScreen && slotBoundsHit(e.payload, spriteScreen)) {
        setCursorOnSprite(true);
        return apply(false);
      }
      setCursorOnSprite(false);
      apply(true);
    });

    return () => {
      disposed = true;
      void unlisten.then((f) => f());
      void win.setIgnoreCursorEvents(false);
      setCursorOnSprite(false);
    };
  }, [isDragging, spriteScreen, setCursorOnSprite]);
}
