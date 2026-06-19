import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import * as PIXI from "pixi.js";
import type { PetInteractions } from "./usePetInteractions";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { findVisibleBounds } from "@/pet/petAlphaHitTest";

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
 * 上；17b 替 slot 内 children 为 Live2DSprite 时不重接。hook 返回 `{ slotRef, appRef }`
 * 暴露给 18 `usePetLive2D` 接力（18 design §6.1 / §6.2）。
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

export interface UsePixiAvatarSlotHandles {
  /** avatar-slot Container ref；mount 完成后 .current = slot，cleanup 时回 null。 */
  slotRef: RefObject<PIXI.Container | null>;
  /**
   * PIXI Application 实例。**用 useState 而不是 ref**：PIXI 异步 init 完成时下游 hook
   * （`usePetLive2D`）才能收到信号重新跑 useEffect 加载 Live2DSprite——ref.current 赋值不
   * 触发 React render，下游 useEffect 永远错过 ready 窗口。
   */
  app: PIXI.Application | null;
  /**
   * 让下游 hook（如 `usePetLive2D` 在 Live2DSprite ready 后）触发重新 alpha-scan 锁定 visible bounds。
   *
   * 为什么需要：alpha-scan 是 one-shot 锁定（防抖动），首次锁定时 slot 内是占位 Graphics + Text
   * （bounds = 占位 ~160×30），Hiyori 加载替换 children 后 anchor 矩形仍停留在占位 bounds →
   * ActionBar 锚错位（贴占位顶 = Hiyori 头部偏中位置导致重叠）。`usePetLive2D` `await sprite.ready`
   * 后调此方法，清掉 cachedOffset 让 ticker 下一帧重新扫定到真 Hiyori visible bounds。
   */
  invalidateAnchor: () => void;
  /**
   * 18b Win mixed DPR mitigation（issue 012）：alpha-scan readPixels 在 Win 多屏不同 DPR
   * 配置下取错位像素 / 全空白时整体降级到 17a 矩形行为。
   *
   * `usePetPassthrough` 读此 ref，true 时跳过 `alphaHitTest` 改用矩形命中（= 17a）。
   * `invalidateAnchor` 时此 ref 一并重置（占位 → Hiyori 切换可能扫到真像素，重试一次）。
   *
   * mac / Win 单屏 / Win 多屏同 DPR：alpha-scan 正常工作，此 ref 恒 false。
   */
  alphaScanGivenUpRef: RefObject<boolean>;
}

const DRAG_MOVE_THRESHOLD_PX = 5;
const CLICK_MAX_DURATION_MS = 300;
const DRAG_VELOCITY_NORM_PX = 25;

export interface UsePixiAvatarSlotOptions {
  /** 024 · 用 ref 延迟绑定 callbacks，避免 App.tsx hooks 调用顺序循环。 */
  interactionsRef?: RefObject<PetInteractions | null>;
}

export function usePixiAvatarSlot(
  stageRef: RefObject<HTMLDivElement | null>,
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void,
  setIsDragging: (v: boolean) => void,
  options: UsePixiAvatarSlotOptions = {},
): UsePixiAvatarSlotHandles {
  const slotRef = useRef<PIXI.Container | null>(null);
  const [app, setApp] = useState<PIXI.Application | null>(null);
  // ref 而非 useEffect 局部变量：让 invalidateAnchor 能从 hook 顶部清掉
  const cachedOffsetRef = useRef<
    { dx: number; dy: number; w: number; h: number } | null
  >(null);
  // 18b Win mixed DPR mitigation（issue 012）：alpha-scan 在 readPixels 不可用时整体降级
  // 到 17a 矩形行为。useRef 暴露给 usePetPassthrough 同步读，避免再起一条 React state 链路。
  const alphaScanGivenUpRef = useRef(false);
  const interactionsRef = options.interactionsRef;

  const invalidateAnchor = useCallback(() => {
    cachedOffsetRef.current = null;
    // 占位 → Hiyori 切换：Hiyori 像素更多，可能扫到（mac 第一次切换就是这场景）
    // Win mixed DPR 下重试还是会再次触发降级，无副作用
    alphaScanGivenUpRef.current = false;
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    let cancelled = false;
    let appInstance: PIXI.Application | null = null;

    void (async () => {
      const pixi = new PIXI.Application();
      await pixi.init({
        backgroundAlpha: 0,
        resizeTo: stage,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
        // 18 · 让 alpha hit-test 能 readPixels 取上一帧 framebuffer alpha
        // （Live2D 形象不透明像素轮廓判定，让 cursor 在透明区精确穿透）
        preserveDrawingBuffer: true,
      });

      // StrictMode 双 mount 防泄漏：cancelled flag 在 cleanup 设 true，
      // 异步 init 完成后若已取消则直接 destroy，不挂 canvas
      if (cancelled) {
        pixi.destroy(true, { children: true, texture: true });
        return;
      }
      appInstance = pixi;
      stage.appendChild(pixi.canvas);

      // —— avatar slot Container：占位 children 等价重现 016 div ——
      const { accent, accentFg } = readAccentColors();
      const slot = new PIXI.Container();
      slot.label = "avatar-slot";
      slot.eventMode = "static";
      slot.cursor = "grab";
      // hitArea 由 ticker 内每帧从 slot.getBounds() 推出（跟随 17b Live2DSprite 加载 / 改尺寸时
      // 自动同步），让 PIXI 事件系统总是用与视觉一致的命中区。初始给一个最小占位避免事件丢
      slot.hitArea = new PIXI.Rectangle(-1, -1, 2, 2);

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
      slotRef.current = slot;
      // PIXI app + slot 都已就绪 → 触发 React render，让 usePetLive2D 收到 app 非 null 信号
      setApp(pixi);

      // —— sprite world position 上报 ——
      //
      // 18 · alpha-scan 一次锁定 visible bounds 在 cachedOffsetRef 内（hook 顶部 useRef，
      // 让 invalidateAnchor 从外部清掉触发重扫）。drag 时只更新 slot.x/y，spriteScreen 跟随。
      const dpr = window.devicePixelRatio || 1;

      const computeBounds = () => {
        const cached = cachedOffsetRef.current;
        if (cached) {
          return {
            x: slot.x + cached.dx,
            y: slot.y + cached.dy,
            w: cached.w,
            h: cached.h,
          };
        }
        // alpha-scan 还没成功锁定 → 用 slot.getBounds() fallback
        const b = slot.getBounds();
        return { x: b.minX, y: b.minY, w: b.width, h: b.height };
      };

      const emitSpritePos = () => {
        const { x, y, w, h } = computeBounds();
        // React state 用 CSS px（驱动 ActionBar 定位）
        setSpriteScreen({ x, y, w, h });
        // Rust invoke 用物理像素（Rust 端拼 monitors union 起点）
        if (isTauri()) {
          void invoke("update_sprite_pos", {
            x: Math.round(x * dpr),
            y: Math.round(y * dpr),
            w: Math.round(w * dpr),
            h: Math.round(h * dpr),
          });
        }
      };

      // —— ticker 持续同步 + alpha-scan 推 visible bounds 一次锁定 ——
      //
      // alpha-scan 仅在 `cachedOffsetRef.current` 还未锁定时尝试（每帧 ticker 调用 → 一旦
      // 扫到有效 visible bounds 就锁住，之后永不重扫）。这样：
      // - 第一次扫成功后锁定 visible 区域相对 slot.position 的 offset，spriteScreen 紧贴
      //   Hiyori 像素轮廓，gap 自动适配
      // - 之后 drag 时只更新 slot.x/y，spriteScreen 跟随
      // - **不重扫** → 没有 motion 引起的 visible bounds 抖动 → ActionBar / bubble 不抖
      // - **占位 → Hiyori 切换**：`usePetLive2D` await sprite.ready 后调 `invalidateAnchor()`
      //   清掉锁，下一帧 ticker 重扫到真 Hiyori visible bounds
      //
      // 启动早期 sprite 还没渲染 / 全透明时 scanned 返 null，下一帧 ticker 再试，直到锁定。
      //
      // **Win mixed DPR 兜底**（issue 012）：Win 多屏不同 DPR 配置下 `gl.readPixels` 读到的
      // 像素位置跟 cursor logical px 坐标系不对齐。用户实测分支：`findVisibleBounds` 返
      // **null**（区域内一个 alpha > threshold 像素都没扫到，DPR 错位读到的全是空白）。
      //
      // 连续 ALPHA_SCAN_NULL_FRAMES 帧都返 null → 视为 readPixels 在 Win mixed DPR 下完全
      // 不可用，设 `alphaScanGivenUpRef.current = true`。下游 `usePetPassthrough` 跳过
      // `alphaHitTest` 改用矩形命中（= 17a 行为）。`computeBounds()` 也自然 fallback 到
      // `slot.getBounds()`（cachedOffset 没设）。整套等价 17a。
      //
      // **不能用 width ratio 判别**：mac 上 Hiyori 头+身体像素轮廓 width 占 slot 矩形
      // ~0.29（跟 issue 012 README 实测 Win 错位 width 91/320=0.28 几乎一样），用 ratio
      // 判别会误伤 mac 正常 alpha-scan。所以只用"持续返 null"这一信号。
      let lastBounds = { x: NaN, y: NaN, w: NaN, h: NaN };
      let nullScanCount = 0;
      const ALPHA_SCAN_NULL_FRAMES = 30; // 0.5s @ 60Hz

      const syncBoundsIfChanged = () => {
        if (!cachedOffsetRef.current && !alphaScanGivenUpRef.current) {
          const slotBounds = slot.getBounds();
          if (slotBounds.width > 0 && slotBounds.height > 0) {
            const scanned = findVisibleBounds(pixi, {
              x: slotBounds.minX,
              y: slotBounds.minY,
              w: slotBounds.width,
              h: slotBounds.height,
            });
            if (scanned) {
              nullScanCount = 0;
              cachedOffsetRef.current = {
                dx: scanned.x - slot.x,
                dy: scanned.y - slot.y,
                w: scanned.w,
                h: scanned.h,
              };
            } else {
              // 区域内无 alpha > threshold 像素 —— 启动早期 sprite 没渲染好正常会出，
              // 但持续出说明 readPixels 在 Win mixed DPR 下取错位读全空白（issue 012 用户实测分支）
              nullScanCount += 1;
              if (nullScanCount >= ALPHA_SCAN_NULL_FRAMES) {
                alphaScanGivenUpRef.current = true;
                if (import.meta.env.DEV) {
                  console.warn(
                    `[anchor] alpha-scan 连续 ${ALPHA_SCAN_NULL_FRAMES} 帧 readPixels 全空白` +
                      `（Win 多屏 mixed DPR 特征），体系降级到 17a 矩形行为 · 见 issue 012`,
                  );
                }
              }
            }
          }
        }

        const { x, y, w, h } = computeBounds();
        if (
          Math.abs(x - lastBounds.x) < 0.5 &&
          Math.abs(y - lastBounds.y) < 0.5 &&
          Math.abs(w - lastBounds.w) < 0.5 &&
          Math.abs(h - lastBounds.h) < 0.5
        ) {
          return;
        }
        lastBounds = { x, y, w, h };
        setSpriteScreen({ x, y, w, h });
        if (isTauri()) {
          void invoke("update_sprite_pos", {
            x: Math.round(x * dpr),
            y: Math.round(y * dpr),
            w: Math.round(w * dpr),
            h: Math.round(h * dpr),
          });
        }
        // 同步更新 PIXI hitArea（slot 局部坐标 = 世界 - slot.position；slot 没缩放）
        slot.hitArea = new PIXI.Rectangle(x - slot.x, y - slot.y, w, h);
      };
      pixi.ticker.add(syncBoundsIfChanged);

      // —— pointer handler：click vs drag 区分（024） ——
      let pointerStart: { x: number; y: number } | null = null;
      let slotStart: { x: number; y: number } | null = null;
      let lastPointer: { x: number; y: number } | null = null;
      let downTimestamp = 0;
      let dragArmed = false;

      function clampDragNorm(v: number): number {
        return Math.max(-1, Math.min(1, v));
      }

      slot.on("pointerdown", (e) => {
        pointerStart = { x: e.global.x, y: e.global.y };
        lastPointer = { x: e.global.x, y: e.global.y };
        slotStart = { x: slot.x, y: slot.y };
        downTimestamp = performance.now();
        dragArmed = false;
        // 不立即 setIsDragging；等 globalpointermove 超阈值再进 drag
      });

      slot.on("globalpointermove", (e) => {
        if (!pointerStart || !slotStart || !lastPointer) return;
        const dx = e.global.x - pointerStart.x;
        const dy = e.global.y - pointerStart.y;

        if (!dragArmed) {
          if (Math.hypot(dx, dy) > DRAG_MOVE_THRESHOLD_PX) {
            dragArmed = true;
            setIsDragging(true);
            slot.cursor = "grabbing";
          } else {
            return;
          }
        }

        slot.x = slotStart.x + dx;
        slot.y = slotStart.y + dy;

        const onSlotDragMove = interactionsRef?.current?.onSlotDragMove;
        if (onSlotDragMove) {
          // 024 修正：传瞬时速度（每帧位移），而非从 pointerdown 开始的累积位移。
          const dvx = e.global.x - lastPointer.x;
          const dvy = e.global.y - lastPointer.y;
          onSlotDragMove(clampDragNorm(dvx / DRAG_VELOCITY_NORM_PX), clampDragNorm(dvy / DRAG_VELOCITY_NORM_PX));
        }
        lastPointer = { x: e.global.x, y: e.global.y };

        // AC-5 修复：前端不节流，每帧 emit；Rust update_sprite_pos 内同步即时
        // 算 bubble position 并 set_position（不等 016 follow loop 16ms tick），
        // 消除"前端节流 16ms + Rust tick 16ms 串联"的视觉卡顿
        emitSpritePos();
      });

      const endPointer = (e: PIXI.FederatedPointerEvent) => {
        if (!pointerStart || !slotStart) return;
        const duration = performance.now() - downTimestamp;
        const dx = e.global.x - pointerStart.x;
        const dy = e.global.y - pointerStart.y;
        const dist = Math.hypot(dx, dy);
        const wasClick = !dragArmed && duration < CLICK_MAX_DURATION_MS && dist < DRAG_MOVE_THRESHOLD_PX;

        pointerStart = null;
        slotStart = null;

        if (dragArmed) {
          setIsDragging(false);
          slot.cursor = "grab";
          emitSpritePos(); // commit 最终位置
        }
        dragArmed = false;

        if (wasClick) {
          interactionsRef?.current?.onSlotClick?.(e);
        }
      };
      slot.on("pointerup", endPointer);
      slot.on("pointerupoutside", endPointer);

      // AC-6 修复：原 PIXI slot.on("pointerover/out") → setHoverActionBar
      //   在 cursor 离开 sprite 后 setIgnoreCursorEvents 切回 true，webview 不再
      //   收 pointer 事件，PIXI pointerout 不再触发 → hover state stuck 在 true。
      //   改由 usePetPassthrough 用 Rust 60Hz cursor channel 驱动 cursorOnSprite。

      // mount 完成同步发一次（填 Rust cache + React state，避免 follow loop 早起 tick 读到 None）
      emitSpritePos();
    })();

    return () => {
      cancelled = true;
      if (appInstance) {
        try {
          if (stage.contains(appInstance.canvas)) stage.removeChild(appInstance.canvas);
        } catch {
          // ignore: dom 可能已 detached
        }
        appInstance.destroy(true, { children: true, texture: true });
        appInstance = null;
      }
      slotRef.current = null;
      setApp(null);
      // cleanup 时清空 React state（避免残留旧 sprite 位置驱动 ActionBar）
      setSpriteScreen(null);
      setIsDragging(false);
    };
  }, [stageRef, setSpriteScreen, setIsDragging]);

  return { slotRef, app, invalidateAnchor, alphaScanGivenUpRef };
}
