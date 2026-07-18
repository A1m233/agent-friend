import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import { slotBoundsHit } from "./slotBoundsHit";

type PassthroughReason =
  | "disabled"
  | "initial"
  | "dragging"
  | "dom-hit"
  | "sprite-hit"
  | "sprite-transparent"
  | "outside";

const PET_PASSTHROUGH_DEBUG_STORAGE_KEY = "agent-friend.debug.pet-passthrough";

function describeHitTarget(el: Element | null): string | null {
  if (!el) return null;

  const tag = el.tagName.toLowerCase();
  const id = el.id ? `#${el.id}` : "";
  const dataHit = el.getAttribute("data-hit");
  const dataHitPart = dataHit ? `[data-hit=${dataHit}]` : "[data-hit]";
  return `${tag}${id}${dataHitPart}`;
}

function shouldLogPassthroughDiagnostic(): boolean {
  if (!import.meta.env.DEV) return false;
  try {
    return window.localStorage.getItem(PET_PASSTHROUGH_DEBUG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * 透明窗鼠标穿透 hit-test（M10.2 spike → 17a 升级到 PIXI 整屏 overlay 形态 → 18 升级到 alpha hit-test）。
 *
 * 原理：Rust 用内置 cursor_position 以 ~60fps 把"光标相对 pet content 区的逻辑坐标"
 * 通过 `pet://cursor` 事件喂给前端；这里据此切原生 cursor passthrough——空白透明区
 * 穿透到桌面，sprite / 操作栏可交互。
 *
 * 18 升级（design §3.4）：sprite 命中判定从矩形 `slotBoundsHit` 升级为 alpha hit-test
 * （`alphaHitTest`）—— Live2D 形象透明区（手脚空隙 / 四角空白）alpha < threshold 时不命中
 * → 精确穿透到下方 app；换 Live2D 模型零硬编码（直接从 framebuffer 像素推）。spriteScreen
 * 矩形仍保留作 fast-reject region：cursor 不在矩形内时直接 short-circuit，避免无谓的
 * GL readPixels（每帧 1 像素 GL fence 虽然小，但叠加 60Hz 仍值得过滤）。
 *
 * 17a 历史：
 *
 * 1. **isDragging 互锁**：drag 期间锁定可交互状态，不再 toggle，
 *    防止快速划过空白区时 webview 失去 pointermove 导致 drag 中途断（design §3.5）
 * 2. **DOM data-hit 优先**：操作栏 DOM 在 PIXI canvas 之上，DOM elementFromPoint 仍能
 *    正确判定操作栏命中（不依赖 PIXI alpha 路径）
 * 3. **sprite 命中**：18 alpha 主路径；app 未就绪时 fallback 到矩形（17a 兜底）
 *
 * 为什么不能用浏览器 mousemove：一旦启用原生鼠标穿透，webview 就收不到
 * 自己的鼠标事件，无法感知"光标再次移入"，必须靠 Rust 的全局光标喂给。
 */

interface Options {
  /** drag 期间锁定原生可交互状态（design §3.5） */
  isDragging: boolean;
  /**
   * sprite anchor 矩形（CSS px，与 `pet://cursor` payload 同坐标系）。
   * 18 · 同时承担：(a) ActionBar/bubble 锚点；(b) alpha hit-test fast-reject region。
   * null 时不命中 sprite。
   */
  spriteScreen: { x: number; y: number; w: number; h: number } | null;
  /**
   * 当前 avatar runtime 的 alpha hit-test。不同 renderer/backend 自己处理 framebuffer 细节；
   * null 时退化到 spriteScreen 矩形命中，保留加载早期可交互性。
   */
  alphaHitTest: ((cursor: { x: number; y: number }) => boolean) | null;
  /**
   * AC-6 修复 · 驱动 ActionBar hover 显隐。
   *
   * 原方案是 PIXI slot.on("pointerover/out") → setHoverActionBar，但 cursor 离开
   * sprite 后切回原生鼠标穿透、webview 不收事件 → PIXI pointerout 不触发
   * → hover state stuck 在 true。
   *
   * 改用本回调，由 Rust 60Hz cursor channel 驱动：cursor 在 sprite alpha 命中时 → true，
   * 在外 / 在 DOM data-hit 上 / drag 期间 → false。
   */
  setCursorOnSprite: (v: boolean) => void;
  /**
   * 18 · dev-only 禁穿透开关 —— 让 inspector / dev tools 可点。
   *
   * true 时：永远保持原生可交互 + 不监 cursor channel + cursorOnSprite=false。
   * pet webview 变实窗，所有鼠标事件 webview 收，下方 app 不再被穿透。
   */
  disabled?: boolean;
}

export function usePetPassthrough({
  isDragging,
  spriteScreen,
  alphaHitTest,
  setCursorOnSprite,
  disabled = false,
}: Options) {
  const latestRef = useRef({
    isDragging,
    spriteScreen,
    alphaHitTest,
    setCursorOnSprite,
  });
  latestRef.current = {
    isDragging,
    spriteScreen,
    alphaHitTest,
    setCursorOnSprite,
  };

  useEffect(() => {
    if (!isTauri()) return; // 浏览器 web 调试下无桌面能力，跳过

    let ignored: boolean | null = null;
    let disposed = false;
    let lastDiagnosticKey: string | null = null;

    const apply = (
      shouldIgnore: boolean,
      reason: PassthroughReason,
      details: Record<string, unknown> = {},
    ) => {
      if (shouldLogPassthroughDiagnostic()) {
        const diagnosticKey = `${shouldIgnore}:${reason}`;
        if (diagnosticKey !== lastDiagnosticKey) {
          lastDiagnosticKey = diagnosticKey;
          console.debug("[pet][passthrough]", {
            ignored: shouldIgnore,
            reason,
            ...details,
          });
        }
      }
      if (shouldIgnore === ignored) return;
      ignored = shouldIgnore;
      void invoke("set_pet_cursor_passthrough", { ignore: shouldIgnore }).catch((error) => {
        console.warn("[pet][passthrough] native toggle failed:", error);
      });
    };

    // dev-only · 禁穿透分支：让 inspector / dev tools 鼠标事件正常
    if (disabled) {
      apply(false, "disabled");
      latestRef.current.setCursorOnSprite(false);
      return () => {
        disposed = true;
        // Fail open: pet 是整屏透明 overlay，异常卸载/HMR 后必须恢复点击穿透，
        // 否则透明窗口会变成吃掉全桌面点击的实窗。
        void invoke("set_pet_cursor_passthrough", { ignore: true })
          .catch((error) => console.warn("[pet][passthrough] cleanup fail-open failed:", error));
        latestRef.current.setCursorOnSprite(false);
      };
    }

    apply(true, "initial"); // 初始默认穿透：空白处不挡桌面

    const unlisten = listen<{ x: number; y: number }>("pet://cursor", (e) => {
      if (disposed) return;
      const latest = latestRef.current;
      // (1) drag 期间锁定 false；hover 状态从 cursorOnSprite 维度看是 "不算 hover"
      //     （但 ActionBar 显隐由 App.tsx 综合 isDragging || cursorOnSprite || hoverActionBarDom 决定）
      if (latest.isDragging) {
        latest.setCursorOnSprite(false);
        return apply(false, "dragging", { cursor: e.payload });
      }
      // (2) DOM data-hit 优先（操作栏 DOM 在 canvas 之上）
      const el = document.elementFromPoint(e.payload.x, e.payload.y);
      const hitTarget = el?.closest("[data-hit]") ?? null;
      if (hitTarget) {
        setCursorOnSprite(false); // cursor 在 ActionBar 上，不在 sprite 上
        return apply(false, "dom-hit", {
          cursor: e.payload,
          target: describeHitTarget(hitTarget),
        });
      }
      // (3) sprite alpha hit-test 主路径（18 升级；spriteScreen 矩形作 fast reject）
      //     各 backend/runtime 自己处理 Pixi7/Pixi8 framebuffer 细节，null 时退化到矩形。
      if (latest.spriteScreen && slotBoundsHit(e.payload, latest.spriteScreen)) {
        const spriteDetails = {
          cursor: e.payload,
          spriteScreen: latest.spriteScreen,
          hasAlphaHitTest: Boolean(latest.alphaHitTest),
        };
        const hit = latest.alphaHitTest
          ? latest.alphaHitTest(e.payload)
          : true; // runtime 未就绪时退化到矩形（17a 兜底，加载早期）
        if (hit) {
          setCursorOnSprite(true);
          return apply(false, "sprite-hit", spriteDetails);
        }
        setCursorOnSprite(false);
        return apply(true, "sprite-transparent", spriteDetails);
      }
      setCursorOnSprite(false);
      apply(true, "outside", { cursor: e.payload });
    });

    return () => {
      disposed = true;
      void unlisten.then((f) => f());
      // Fail open: cleanup 可能来自 React error boundary / HMR，而 pet 窗是整屏透明 overlay。
      // `true` = 忽略鼠标事件，让点击穿透到桌面，避免异常退出后锁住其它应用窗口。
      void invoke("set_pet_cursor_passthrough", { ignore: true })
        .catch((error) => console.warn("[pet][passthrough] cleanup fail-open failed:", error));
      latestRef.current.setCursorOnSprite(false);
    };
  }, [disabled, setCursorOnSprite]);
}
