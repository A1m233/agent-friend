import { useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";

/**
 * 透明窗鼠标穿透 hit-test（M10.2 spike）。
 *
 * 原理：Rust 用内置 cursor_position 以 ~60fps 把"光标相对桌宠窗内容区的逻辑坐标"
 * 通过 `pet://cursor` 事件喂给前端；这里据此判断光标是否落在实心区域，
 * 动态切 `setIgnoreCursorEvents`——空白透明区穿透到桌面，形象/操作栏可交互。
 *
 * 为什么不能用浏览器 mousemove：一旦 setIgnoreCursorEvents(true)，webview
 * 就收不到自己的鼠标事件，无法感知"光标再次移入"，必须靠 Rust 的全局光标喂给。
 *
 * 命中判定是一道"缝"：本期用 DOM 几何（elementFromPoint + [data-hit]，
 * border-radius 会裁剪指针命中，所以圆形象判定天然贴形状）；未来上 Live2D 时
 * 把 hitTest 换成对 PixiJS canvas 的 gl.readPixels alpha 采样即可，plumbing 不变。
 */
function domHitTest(x: number, y: number): boolean {
  const el = document.elementFromPoint(x, y);
  return !!el?.closest("[data-hit]");
}

export function usePetPassthrough(hitTest: (x: number, y: number) => boolean = domHitTest) {
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
      apply(!hitTest(e.payload.x, e.payload.y));
    });

    return () => {
      disposed = true;
      void unlisten.then((f) => f());
      void win.setIgnoreCursorEvents(false);
    };
  }, [hitTest]);
}
