import { useCallback, useEffect, useMemo, type RefObject } from "react";
import * as PIXI from "pixi.js";
import { listen } from "@tauri-apps/api/event";
import { Priority } from "easy-live2d";
import type { Live2DSprite } from "easy-live2d";
import { isTauri } from "@/utils/tauri";
import { composePetRender } from "@/pet/composePetRender";
import { GazeSource } from "@/pet/sources/GazeSource";
import { TapReactionSource } from "@/pet/sources/TapReactionSource";
import { DragReactionSource } from "@/pet/sources/DragReactionSource";
import type { TextCadenceMouthDriver } from "@/pet/MouthDriver";
import { PET_LIVE2D_CONFIG } from "@/pet/live2dConfig";
import { usePetStateStore } from "@/stores/petState";

/**
 * 024 · 把 gaze / tap / drag / lip-sync 四个 ParamSource 统一挂到 Live2D sprite。
 *
 * - 监听 Rust pet://cursor 60Hz 通道驱动 GazeSource
 * - 接收 slot 派发的 click / drag-move 事件驱动 TapReactionSource / DragReactionSource
 * - 把 18 TextCadenceMouthDriver 作为 ParamSource 之一交给 composePetRender 调度
 */
export interface PetInteractions {
  /** PIXI slot 派发的 click（已经 click vs drag 区分）。接收 event 只为与 slot handler 签名对齐。 */
  onSlotClick: (e: PIXI.FederatedPointerEvent) => void;
  /** PIXI slot 派发的 drag 方向（归一化到 [-1, 1]）。 */
  onSlotDragMove: (vx: number, vy: number) => void;
}

function safeUnlisten(fn: (() => void) | null | undefined): void {
  if (!fn) return;
  try {
    fn();
  } catch {
    /* stale-cleanup race ignored */
  }
}

export function usePetInteractions(
  spriteRef: RefObject<Live2DSprite | null>,
  spriteReady: boolean,
  spriteScreen: { x: number; y: number; w: number; h: number } | null,
  isDragging: boolean,
  mouthDriver: TextCadenceMouthDriver,
): PetInteractions {
  const gaze = useMemo(() => new GazeSource(), []);
  const tap = useMemo(() => new TapReactionSource(), []);
  const drag = useMemo(() => new DragReactionSource(), []);

  // drag 与 gaze 互斥
  useEffect(() => {
    drag.setDragging(isDragging);
    gaze.setActive(!isDragging);
  }, [isDragging, drag, gaze]);

  // composer 装载（等 sprite ready）
  useEffect(() => {
    const sprite = spriteRef.current;
    if (!spriteReady || !sprite) return;
    if (import.meta.env.DEV) {
      console.info("[interactions] composer attached, spriteReady=", spriteReady);
    }
    const detach = composePetRender(sprite, [gaze, tap, drag, mouthDriver]);
    return () => {
      if (import.meta.env.DEV) {
        console.info("[interactions] composer detached");
      }
      detach();
    };
  }, [spriteReady, spriteRef, gaze, tap, drag, mouthDriver]);

  // cursor channel 副订阅 → gaze
  useEffect(() => {
    if (!isTauri() || !spriteScreen) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    void listen<{ x: number; y: number }>("pet://cursor", (e) => {
      gaze.updateCursor(e.payload.x, e.payload.y, spriteScreen);
    }).then((u) => {
      if (cancelled) safeUnlisten(u);
      else unlisten = u;
    });
    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, [spriteScreen, gaze]);

  const onSlotClick = useCallback(
    (_e: PIXI.FederatedPointerEvent) => {
      const phase = usePetStateStore.getState().phase;
      if (phase === "speaking") return; // speaking 抢镜
      tap.fire();
      void spriteRef.current?.startMotion({
        group: PET_LIVE2D_CONFIG.motionGroups.tap ?? "Idle",
        no: PET_LIVE2D_CONFIG.motionNo.tap ?? 0,
        priority: Priority.Normal,
      });
    },
    [spriteRef, tap],
  );

  const onSlotDragMove = useCallback(
    (vx: number, vy: number) => {
      drag.updateDragDirection(vx, vy);
    },
    [drag],
  );

  return { onSlotClick, onSlotDragMove };
}
