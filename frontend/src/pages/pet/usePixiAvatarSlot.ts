import { useEffect, type RefObject } from "react";
import * as PIXI from "pixi.js";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";

/**
 * 17a · PIXI canvas + avatar-slot Container 生命周期 hook（design §5.1 ~ §5.3）。
 *
 * 职责：
 * - 在 stageRef 内挂 PIXI v8 Application（resizeTo: stageRef + autoDensity + DPR；
 *   StrictMode 双 mount cleanup pattern：cancelled flag + destroy 顺序沿
 *   macOS spike §5.5.7 / Win spike §6.4.4.7 同款）
 * - 建 avatar-slot `PIXI.Container`，内塞占位 Graphics(圆) + Text("占位形象")，
 *   等价重现 016 div 视觉（颜色从 CSS var 读、theme-aware）
 * - drag handler（pointerdown / globalpointermove / pointerup / pointerupoutside）
 * - sprite world position 上报：mount 完成同步 + drag 期间**每帧都发**（无节流，
 *   配合 Rust update_sprite_pos 同步即时 bubble.set_position，消除 016 16ms tick 视觉滞后）
 *
 * 17b 接缝（design §7）：5 个接缝点中 #2 / #3 / #4 / #5 全部挂在外层 slot Container
 * 上；17b 替 slot 内 children 为 Live2DModel 时不重接。
 *
 * **AC-6 hover 不在本 hook 内承载**：PIXI pointerover/out 在 setIgnoreCursorEvents 切回
 * true（cursor 离开 sprite 之后）后不再触发，会导致 hover state stuck；改由
 * `usePetPassthrough` 用 Rust 60Hz cursor 数据驱动 `setCursorOnSprite`（那个 channel 一直跑）。
 */

const SLOT_RADIUS = 80; // 与 016 `h-40 w-40 rounded-full`（直径 160）等价
const PLACEHOLDER_TEXT = "占位形象";

/** 从 `:root` CSS var 读 hex 颜色（theme-aware）；无效时返回 fallback。 */
function readAccentColors(): { accent: number; accentFg: number } {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return { accent: 0x4f46e5, accentFg: 0xffffff };
  }
  const cs = getComputedStyle(document.documentElement);
  const parse = (name: string, fallback: number): number => {
    const raw = cs.getPropertyValue(name).trim();
    if (!raw.startsWith("#")) return fallback;
    const n = parseInt(raw.slice(1), 16);
    return Number.isFinite(n) ? n : fallback;
  };
  return {
    accent: parse("--accent", 0x4f46e5),
    accentFg: parse("--accent-fg", 0xffffff),
  };
}

export function usePixiAvatarSlot(
  stageRef: RefObject<HTMLDivElement | null>,
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void,
  setIsDragging: (v: boolean) => void,
) {
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    let app: PIXI.Application | null = null;
    let cancelled = false;

    void (async () => {
      const pixi = new PIXI.Application();
      await pixi.init({
        backgroundAlpha: 0,
        resizeTo: stage,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
      });

      // StrictMode 双 mount 防泄漏：cancelled flag 在 cleanup 设 true，
      // 异步 init 完成后若已取消则直接 destroy，不挂 canvas
      if (cancelled) {
        pixi.destroy(true, { children: true, texture: true });
        return;
      }
      app = pixi;
      stage.appendChild(pixi.canvas);

      // —— avatar slot Container：占位 children 等价重现 016 div ——
      const { accent, accentFg } = readAccentColors();
      const slot = new PIXI.Container();
      slot.label = "avatar-slot";
      slot.eventMode = "static";
      slot.cursor = "grab";

      const circle = new PIXI.Graphics();
      circle.circle(0, 0, SLOT_RADIUS).fill({ color: accent });
      slot.addChild(circle);

      const text = new PIXI.Text({
        text: PLACEHOLDER_TEXT,
        style: {
          fontSize: 14,
          fill: accentFg,
          fontFamily: "system-ui, -apple-system, sans-serif",
        },
      });
      text.anchor.set(0.5);
      text.x = 0;
      text.y = 0;
      slot.addChild(text);

      // 初始位置 = 屏幕中心
      slot.x = pixi.screen.width / 2;
      slot.y = pixi.screen.height / 2;
      pixi.stage.addChild(slot);

      // —— sprite world position 上报 ——
      const dpr = window.devicePixelRatio || 1;
      const emitSpritePos = () => {
        const b = slot.getBounds();
        // React state 用 CSS px（驱动 ActionBar 定位）
        setSpriteScreen({ x: b.minX, y: b.minY, w: b.width, h: b.height });
        // Rust invoke 用物理像素（Rust 端拼 monitors union 起点）
        if (isTauri()) {
          void invoke("update_sprite_pos", {
            x: Math.round(b.minX * dpr),
            y: Math.round(b.minY * dpr),
            w: Math.round(b.width * dpr),
            h: Math.round(b.height * dpr),
          });
        }
      };

      // —— drag handler ——
      let pointerStart: { x: number; y: number } | null = null;
      let slotStart: { x: number; y: number } | null = null;

      slot.on("pointerdown", (e) => {
        pointerStart = { x: e.global.x, y: e.global.y };
        slotStart = { x: slot.x, y: slot.y };
        setIsDragging(true);
        slot.cursor = "grabbing";
      });

      slot.on("globalpointermove", (e) => {
        if (!pointerStart || !slotStart) return;
        slot.x = slotStart.x + (e.global.x - pointerStart.x);
        slot.y = slotStart.y + (e.global.y - pointerStart.y);
        // AC-5 修复：前端不节流，每帧 emit；Rust update_sprite_pos 内同步即时
        // 算 bubble position 并 set_position（不等 016 follow loop 16ms tick），
        // 消除"前端节流 16ms + Rust tick 16ms 串联"的视觉卡顿
        emitSpritePos();
      });

      const endDrag = () => {
        if (!pointerStart) return;
        pointerStart = null;
        slotStart = null;
        setIsDragging(false);
        slot.cursor = "grab";
        emitSpritePos(); // commit 最终位置
      };
      slot.on("pointerup", endDrag);
      slot.on("pointerupoutside", endDrag);

      // AC-6 修复：原 PIXI slot.on("pointerover/out") → setHoverActionBar
      //   在 cursor 离开 sprite 后 setIgnoreCursorEvents 切回 true，webview 不再
      //   收 pointer 事件，PIXI pointerout 不再触发 → hover state stuck 在 true。
      //   改由 usePetPassthrough 用 Rust 60Hz cursor channel 驱动 cursorOnSprite。

      // mount 完成同步发一次（填 Rust cache + React state，避免 follow loop 早起 tick 读到 None）
      emitSpritePos();
    })();

    return () => {
      cancelled = true;
      if (app) {
        try {
          if (stage.contains(app.canvas)) stage.removeChild(app.canvas);
        } catch {
          // ignore: dom 可能已 detached
        }
        app.destroy(true, { children: true, texture: true });
        app = null;
      }
      // cleanup 时清空 React state（避免残留旧 sprite 位置驱动 ActionBar）
      setSpriteScreen(null);
      setIsDragging(false);
    };
  }, [stageRef, setSpriteScreen, setIsDragging]);
}
