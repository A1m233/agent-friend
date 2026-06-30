import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import * as PIXI7 from "pixi7";
import type { Spine as Spine38 } from "pixi-spine";
import type {
  AvatarModelAction,
  AvatarRuntimePhase,
  SpineAvatarModelEntry,
} from "@/avatar-models";
import {
  resolveAnimationName,
  resolveSpineActionMapping,
} from "@/pet/avatarRuntime/actionMapping";
import {
  alphaHitTestFromApp,
  findVisibleBoundsFromApp,
} from "@/pet/avatarRuntime/framebuffer";
import type {
  AvatarActionResult,
  PetAvatarRuntime,
} from "@/pet/avatarRuntime/types";
import type { PixelRegion } from "@/pet/petAlphaHitTest";
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import { isTauri } from "@/utils/tauri";
import {
  boundsToRegion,
  fitSpine38ToStage,
  type Spine38StageViewport,
} from "./spine38Fit";
import { sampleSpine38CanonicalLayoutBounds } from "./spine38LayoutBounds";
import { loadSpine38Model } from "./spine38Loader";

interface Props {
  entry: SpineAvatarModelEntry;
  initialAnchorPoint: { x: number; y: number } | null;
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setActionBarAnchor: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setIsDragging: (v: boolean) => void;
  onRuntimeChange: (runtime: PetAvatarRuntime | null) => void;
}

const DRAG_MOVE_THRESHOLD_PX = 5;
const CLICK_MAX_DURATION_MS = 300;
const ALPHA_SCAN_NULL_FRAMES = 30;

function getGlobalPoint(event: unknown): { x: number; y: number } | null {
  const candidate = event as { global?: { x?: unknown; y?: unknown } };
  const x = candidate.global?.x;
  const y = candidate.global?.y;
  return typeof x === "number" && typeof y === "number" ? { x, y } : null;
}

function getTickerDeltaMs(tickerArg: unknown): number {
  if (typeof tickerArg === "number") return tickerArg * (1000 / 60);
  const ticker = tickerArg as { deltaMS?: unknown };
  return typeof ticker.deltaMS === "number" ? ticker.deltaMS : 1000 / 60;
}

function setSlotHitArea(slot: PIXI7.Container, region: PixelRegion): void {
  slot.hitArea = new PIXI7.Rectangle(region.x, region.y, region.w, region.h);
}

function sameRegion(a: PixelRegion | null, b: PixelRegion): boolean {
  return (
    a !== null &&
    Math.abs(a.x - b.x) < 0.5 &&
    Math.abs(a.y - b.y) < 0.5 &&
    Math.abs(a.w - b.w) < 0.5 &&
    Math.abs(a.h - b.h) < 0.5
  );
}

function offsetRegionFromBase(region: PixelRegion, base: PixelRegion): PixelRegion {
  return {
    x: region.x - base.x,
    y: region.y - base.y,
    w: region.w,
    h: region.h,
  };
}

function applyRegionOffset(base: PixelRegion, offset: PixelRegion): PixelRegion {
  return {
    x: base.x + offset.x,
    y: base.y + offset.y,
    w: offset.w,
    h: offset.h,
  };
}

function translateRegion(region: PixelRegion, dx: number, dy: number): PixelRegion {
  return {
    x: region.x + dx,
    y: region.y + dy,
    w: region.w,
    h: region.h,
  };
}

function centerRegionOnPoint(
  slot: PIXI7.Container,
  region: PixelRegion,
  point: { x: number; y: number } | null,
): PixelRegion {
  if (!point) return region;
  const dx = point.x - (region.x + region.w / 2);
  const dy = point.y - (region.y + region.h / 2);
  if (
    !Number.isFinite(dx) ||
    !Number.isFinite(dy) ||
    (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5)
  ) {
    return region;
  }
  slot.x += dx;
  slot.y += dy;
  return translateRegion(region, dx, dy);
}

function toScreenRegion(
  slot: PIXI7.Container,
  spine: Spine38,
  bounds: PixelRegion,
): PixelRegion {
  return {
    x: slot.x + (spine.x + bounds.x) * slot.scale.x,
    y: slot.y + (spine.y + bounds.y) * slot.scale.y,
    w: bounds.w * slot.scale.x,
    h: bounds.h * slot.scale.y,
  };
}

function toSlotHitRegion(spine: Spine38, bounds: PixelRegion): PixelRegion {
  return {
    x: spine.x + bounds.x,
    y: spine.y + bounds.y,
    w: bounds.w,
    h: bounds.h,
  };
}

function reportSpritePosition(bounds: PixelRegion): void {
  if (!isTauri()) return;
  const dpr = window.devicePixelRatio || 1;
  void invoke("update_sprite_pos", {
    x: Math.round(bounds.x * dpr),
    y: Math.round(bounds.y * dpr),
    w: Math.round(bounds.w * dpr),
    h: Math.round(bounds.h * dpr),
  }).catch((error: unknown) =>
    console.warn("[spine38] update_sprite_pos failed:", error),
  );
}

function readPositiveScreenSizeValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function resolveInitialStageViewport(
  app: PIXI7.Application<HTMLCanvasElement>,
): Spine38StageViewport {
  const screenWidth =
    readPositiveScreenSizeValue(window.screen?.availWidth) ??
    readPositiveScreenSizeValue(window.screen?.width);
  const screenHeight =
    readPositiveScreenSizeValue(window.screen?.availHeight) ??
    readPositiveScreenSizeValue(window.screen?.height);

  return {
    x: 0,
    y: 0,
    w: screenWidth ? Math.min(app.screen.width, screenWidth) : app.screen.width,
    h: screenHeight ? Math.min(app.screen.height, screenHeight) : app.screen.height,
  };
}

export function Spine38AvatarSurface({
  entry,
  initialAnchorPoint,
  setSpriteScreen,
  setActionBarAnchor,
  setIsDragging,
  onRuntimeChange,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const appRef = useRef<PIXI7.Application<HTMLCanvasElement> | null>(null);
  const slotRef = useRef<PIXI7.Container | null>(null);
  const spineRef = useRef<Spine38 | null>(null);
  const animationsRef = useRef<string[]>([]);
  const currentPhaseRef = useRef<AvatarRuntimePhase>("idle");
  const layoutBoundsRef = useRef<PixelRegion | null>(null);
  const lastSpriteScreenRef = useRef<PixelRegion | null>(null);
  const actionBarAnchorOffsetRef = useRef<PixelRegion | null>(null);
  const initialAnchorPointRef = useRef(initialAnchorPoint);
  const initialLayoutAnchorAlignedRef = useRef(false);
  const initialVisibleAnchorAlignedRef = useRef(false);
  const alphaScanReadyRef = useRef(false);
  const alphaScanGivenUpRef = useRef(false);
  const [ready, setReady] = useState(false);

  const playMappedAction = useCallback(
    (action: AvatarModelAction): AvatarActionResult => {
      const spine = spineRef.current;
      if (!spine) return { status: "skipped", reason: "spine runtime not ready" };

      const mapping = resolveSpineActionMapping(entry, action);
      const resolved = resolveAnimationName(
        animationsRef.current,
        mapping.animation,
        entry.spine.defaultAnimation,
      );
      if (!resolved) {
        return { status: "skipped", reason: `no animation available for ${action}` };
      }

      try {
        spine.state.setAnimation(0, resolved.animation, mapping.loop);
        if (
          action === "idle" ||
          action === "thinking" ||
          action === "speaking" ||
          action === "error" ||
          action === "sleep"
        ) {
          currentPhaseRef.current = action;
        } else if (mapping.returnToPhase) {
          const phaseMapping = resolveSpineActionMapping(entry, currentPhaseRef.current);
          const phaseAnimation = resolveAnimationName(
            animationsRef.current,
            phaseMapping.animation,
            entry.spine.defaultAnimation,
          );
          if (phaseAnimation) {
            spine.state.addAnimation(0, phaseAnimation.animation, true, 0);
          }
        }
        if (resolved.fallbackReason && import.meta.env.DEV) {
          console.info("[spine38] animation fallback", {
            action,
            target: mapping.animation,
            resolved: resolved.animation,
            reason: resolved.fallbackReason,
          });
        }
        return {
          status: "played",
          animation: resolved.animation,
          fallbackReason: resolved.fallbackReason,
        };
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        console.warn("[spine38] animation failed:", error);
        return { status: "skipped", reason };
      }
    },
    [entry],
  );

  initialAnchorPointRef.current = initialAnchorPoint;

  const syncBounds = useCallback(() => {
    const slot = slotRef.current;
    const spine = spineRef.current;
    if (!slot || !spine) return;

    const layoutBounds = layoutBoundsRef.current;
    let nextBounds = layoutBounds
      ? toScreenRegion(slot, spine, layoutBounds)
      : boundsToRegion(spine.getBounds()) ?? boundsToRegion(slot.getBounds());
    if (!nextBounds) return;
    if (!initialLayoutAnchorAlignedRef.current) {
      initialLayoutAnchorAlignedRef.current = true;
      nextBounds = centerRegionOnPoint(
        slot,
        nextBounds,
        initialAnchorPointRef.current,
      );
    }
    setSlotHitArea(
      slot,
      layoutBounds
        ? toSlotHitRegion(spine, layoutBounds)
        : {
            x: (nextBounds.x - slot.x) / slot.scale.x,
            y: (nextBounds.y - slot.y) / slot.scale.y,
            w: nextBounds.w / slot.scale.x,
            h: nextBounds.h / slot.scale.y,
          },
    );

    if (sameRegion(lastSpriteScreenRef.current, nextBounds)) return;
    lastSpriteScreenRef.current = nextBounds;
    setSpriteScreen(nextBounds);
    const actionBarAnchorOffset = actionBarAnchorOffsetRef.current;
    const actionBarAnchor = actionBarAnchorOffset
      ? applyRegionOffset(nextBounds, actionBarAnchorOffset)
      : null;
    if (actionBarAnchor) {
      setActionBarAnchor(actionBarAnchor);
    }
    publishAnchorDiagnostics({
      modelId: entry.id,
      backend: entry.backend,
      spriteScreen: nextBounds,
      visibleBounds: actionBarAnchor,
      actionBarAnchor: actionBarAnchor ?? nextBounds,
    });
    reportSpritePosition(nextBounds);
  }, [entry.backend, entry.id, setActionBarAnchor, setSpriteScreen]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    let cancelled = false;
    let app: PIXI7.Application<HTMLCanvasElement> | null = null;
    let nullScanCount = 0;
    let tickerElapsed = 0;

    const setup = async () => {
      setReady(false);
      setSpriteScreen(null);
      setActionBarAnchor(null);
      layoutBoundsRef.current = null;
      lastSpriteScreenRef.current = null;
      actionBarAnchorOffsetRef.current = null;
      initialLayoutAnchorAlignedRef.current = false;
      initialVisibleAnchorAlignedRef.current = false;
      alphaScanReadyRef.current = false;
      alphaScanGivenUpRef.current = false;
      animationsRef.current = [];
      currentPhaseRef.current = "idle";

      app = new PIXI7.Application<HTMLCanvasElement>({
        backgroundAlpha: 0,
        resizeTo: stage,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
        preserveDrawingBuffer: true,
      });
      appRef.current = app;
      stage.appendChild(app.view);

      const slot = new PIXI7.Container();
      slot.eventMode = "static";
      slot.cursor = "grab";
      setSlotHitArea(slot, { x: -1, y: -1, w: 2, h: 2 });
      app.stage.addChild(slot);
      slotRef.current = slot;

      let pointerStart: { x: number; y: number } | null = null;
      let slotStart: { x: number; y: number } | null = null;
      let downTimestamp = 0;
      let dragArmed = false;

      slot.on("pointerdown", (event) => {
        const global = getGlobalPoint(event);
        if (!global) return;
        pointerStart = global;
        slotStart = { x: slot.x, y: slot.y };
        downTimestamp = performance.now();
        dragArmed = false;
      });

      slot.on("globalpointermove", (event) => {
        const global = getGlobalPoint(event);
        if (!global || !pointerStart || !slotStart) return;
        const dx = global.x - pointerStart.x;
        const dy = global.y - pointerStart.y;
        if (!dragArmed) {
          if (Math.hypot(dx, dy) <= DRAG_MOVE_THRESHOLD_PX) return;
          dragArmed = true;
          slot.cursor = "grabbing";
          setIsDragging(true);
          playMappedAction("dragStart");
        }
        slot.x = slotStart.x + dx;
        slot.y = slotStart.y + dy;
        syncBounds();
      });

      const endPointer = (event: unknown) => {
        if (!pointerStart || !slotStart) return;
        const global = getGlobalPoint(event);
        const duration = performance.now() - downTimestamp;
        const dx = global ? global.x - pointerStart.x : 0;
        const dy = global ? global.y - pointerStart.y : 0;
        const dist = Math.hypot(dx, dy);
        const wasClick =
          !dragArmed && duration < CLICK_MAX_DURATION_MS && dist < DRAG_MOVE_THRESHOLD_PX;

        pointerStart = null;
        slotStart = null;

        if (dragArmed) {
          setIsDragging(false);
          slot.cursor = "grab";
          playMappedAction("dragEnd");
          syncBounds();
        }
        dragArmed = false;

        if (wasClick) {
          playMappedAction("tap");
        }
      };
      slot.on("pointerup", endPointer);
      slot.on("pointerupoutside", endPointer);

      const spine = await loadSpine38Model(entry);
      if (cancelled) {
        spine.destroy({ children: true });
        return;
      }
      spine.eventMode = "none";
      slot.addChild(spine);
      spineRef.current = spine;
      animationsRef.current = spine.skeleton.data.animations.map((item) => item.name);
      const layoutBounds = sampleSpine38CanonicalLayoutBounds(
        spine,
        entry,
        animationsRef.current,
      );
      layoutBoundsRef.current = layoutBounds;
      fitSpine38ToStage(
        app,
        slot,
        spine,
        entry.spine.fit,
        layoutBounds,
        resolveInitialStageViewport(app),
      );
      playMappedAction("idle");
      syncBounds();
      setReady(true);

      app.ticker.add((ticker) => {
        tickerElapsed += getTickerDeltaMs(ticker);
        if (tickerElapsed < 250) return;
        tickerElapsed = 0;
        if (!alphaScanReadyRef.current && !alphaScanGivenUpRef.current) {
          const layoutBounds = layoutBoundsRef.current;
          let screenBounds = layoutBounds
            ? toScreenRegion(slot, spine, layoutBounds)
            : boundsToRegion(slot.getBounds());
          let scanned = screenBounds
            ? findVisibleBoundsFromApp(app, screenBounds)
            : null;
          if (scanned) {
            alphaScanReadyRef.current = true;
            nullScanCount = 0;
            if (!initialVisibleAnchorAlignedRef.current) {
              initialVisibleAnchorAlignedRef.current = true;
              const aligned = centerRegionOnPoint(
                slot,
                scanned,
                initialAnchorPointRef.current,
              );
              if (aligned !== scanned) {
                const dx = aligned.x - scanned.x;
                const dy = aligned.y - scanned.y;
                scanned = aligned;
                screenBounds = screenBounds
                  ? translateRegion(screenBounds, dx, dy)
                  : screenBounds;
                lastSpriteScreenRef.current = null;
              }
            }
            actionBarAnchorOffsetRef.current = screenBounds
              ? offsetRegionFromBase(scanned, screenBounds)
              : null;
            setActionBarAnchor(scanned);
            publishAnchorDiagnostics({
              visibleBounds: scanned,
              actionBarAnchor: scanned,
            });
          } else {
            nullScanCount += 1;
            if (nullScanCount >= ALPHA_SCAN_NULL_FRAMES) {
              alphaScanGivenUpRef.current = true;
            }
          }
        }
        syncBounds();
      });
    };

    void setup().catch((error) => {
      console.warn("[spine38] load failed:", error);
      setReady(false);
    });

    return () => {
      cancelled = true;
      setReady(false);
      setIsDragging(false);
      setSpriteScreen(null);
      setActionBarAnchor(null);
      onRuntimeChange(null);
      layoutBoundsRef.current = null;
      lastSpriteScreenRef.current = null;
      actionBarAnchorOffsetRef.current = null;
      initialLayoutAnchorAlignedRef.current = false;
      initialVisibleAnchorAlignedRef.current = false;
      alphaScanReadyRef.current = false;
      alphaScanGivenUpRef.current = false;
      const currentApp = appRef.current;
      const canvas = currentApp?.view;
      if (canvas && stage.contains(canvas)) {
        stage.removeChild(canvas);
      }
      currentApp?.destroy(true, {
        children: true,
        texture: false,
        baseTexture: false,
      });
      appRef.current = null;
      slotRef.current = null;
      spineRef.current = null;
    };
  }, [
    entry,
    onRuntimeChange,
    playMappedAction,
    setIsDragging,
    setActionBarAnchor,
    setSpriteScreen,
    syncBounds,
  ]);

  const runtime = useMemo<PetAvatarRuntime>(
    () => ({
      kind: "spine",
      modelId: entry.id,
      capabilities: entry.capabilities,
      ready,
      setPhase: (phase) => playMappedAction(phase),
      playAction: (action) => playMappedAction(action),
      alphaHitTest: (cursor) => {
        if (alphaScanGivenUpRef.current) return true;
        const app = appRef.current;
        return app ? alphaHitTestFromApp(app, cursor) : false;
      },
    }),
    [entry, playMappedAction, ready],
  );

  useEffect(() => {
    onRuntimeChange(runtime);
    return () => onRuntimeChange(null);
  }, [onRuntimeChange, runtime]);

  return <div ref={stageRef} className="fixed inset-0 overflow-hidden bg-transparent" />;
}
