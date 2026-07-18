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
  findAdaptiveVisibleRegionFromApp,
} from "@/pet/avatarRuntime/framebuffer";
import type {
  AvatarActionResult,
  PetAvatarRuntime,
} from "@/pet/avatarRuntime/types";
import type { PixelRegion } from "@/pet/petAlphaHitTest";
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import { isTauri } from "@/utils/tauri";
import type { PetDisplayProfile } from "@/pages/pet/petDisplayProfile";
import type { PetWebviewDisplayContext } from "@/pages/pet/petWebviewDisplayContext";
import { isFiniteRect } from "@/pages/pet/petGeometry";
import { syncPixiRendererDpr } from "@/pet/avatarRuntime/pixiRendererDpr";
import {
  boundsToRegion,
  composeSpine38DisplayScale,
  fitSpine38ToStage,
  resolveSpine38NormalizationScale,
  type Spine38StageViewport,
} from "./spine38Fit";
import { sampleSpine38CanonicalLayoutBounds } from "./spine38LayoutBounds";
import { loadSpine38Model } from "./spine38Loader";
import {
  applyRegionOffset,
  computeCenteringDelta,
  computePointDelta,
  offsetRegionFromBase,
  offsetPointFromBase,
  resolvePlacementAnchorPoint,
  resolvePlacementAnchorRegion,
  sameRegion,
  translateRegion,
  type PixelPoint,
} from "./spine38Placement";

interface Props {
  entry: SpineAvatarModelEntry;
  initialAnchorPoint: { x: number; y: number } | null;
  displayProfile: PetDisplayProfile;
  displayContext: PetWebviewDisplayContext;
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setActionBarAnchor: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setIsDragging: (v: boolean) => void;
  onRuntimeChange: (runtime: PetAvatarRuntime | null) => void;
}

const DRAG_MOVE_THRESHOLD_PX = 5;
const CLICK_MAX_DURATION_MS = 300;
const ALPHA_SCAN_NULL_FRAMES = 30;
// Future settings slider plugs into this factor without changing model normalization or mini mode.
const DEFAULT_USER_SCALE = 1;

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

function centerRegionOnPoint(
  slot: PIXI7.Container,
  region: PixelRegion,
  point: { x: number; y: number } | null,
): PixelRegion {
  const delta = computeCenteringDelta(region, point);
  if (!delta) return region;
  const { dx, dy } = delta;
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

function toSpineLocalRegion(
  slot: PIXI7.Container,
  spine: Spine38,
  bounds: PixelRegion,
): PixelRegion {
  const scaleX = slot.scale.x || 1;
  const scaleY = slot.scale.y || 1;
  return {
    x: (bounds.x - slot.x) / scaleX - spine.x,
    y: (bounds.y - slot.y) / scaleY - spine.y,
    w: bounds.w / scaleX,
    h: bounds.h / scaleY,
  };
}

function toSpineLocalPoint(
  slot: PIXI7.Container,
  spine: Spine38,
  point: PixelPoint,
): PixelPoint {
  return {
    x: (point.x - slot.x) / (slot.scale.x || 1) - spine.x,
    y: (point.y - slot.y) / (slot.scale.y || 1) - spine.y,
  };
}

function toScreenPoint(
  slot: PIXI7.Container,
  spine: Spine38,
  point: PixelPoint,
): PixelPoint {
  return {
    x: slot.x + (spine.x + point.x) * slot.scale.x,
    y: slot.y + (spine.y + point.y) * slot.scale.y,
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
  displayProfile,
  displayContext,
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
  const lastActionBarAnchorRef = useRef<PixelRegion | null>(null);
  const actionBarAnchorOffsetRef = useRef<PixelRegion | null>(null);
  const visualAnchorOffsetRef = useRef<PixelPoint | null>(null);
  const initialAnchorPointRef = useRef(initialAnchorPoint);
  const displayProfileRef = useRef(displayProfile);
  const displayContextRef = useRef(displayContext);
  const normalizationScaleRef = useRef(1);
  const lastAppliedPlacementKeyRef = useRef<string | null>(null);
  const initialLayoutAnchorAlignedRef = useRef(false);
  const initialVisibleAnchorAlignedRef = useRef(false);
  const alphaScanReadyRef = useRef(false);
  const alphaScanGivenUpRef = useRef(false);
  const [ready, setReady] = useState(false);

  const setActionBarAnchorIfChanged = useCallback(
    (next: PixelRegion | null) => {
      if (next && !isFiniteRect(next)) return;
      if (next === null) {
        if (lastActionBarAnchorRef.current === null) return;
        lastActionBarAnchorRef.current = null;
        setActionBarAnchor(null);
        return;
      }
      if (sameRegion(lastActionBarAnchorRef.current, next)) return;
      lastActionBarAnchorRef.current = next;
      setActionBarAnchor(next);
    },
    [setActionBarAnchor],
  );

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
  displayProfileRef.current = displayProfile;
  displayContextRef.current = displayContext;

  const applyDisplayProfile = useCallback(
    (currentBounds: PixelRegion): PixelRegion => {
      const slot = slotRef.current;
      const spine = spineRef.current;
      if (!slot || !spine) return currentBounds;

      const profile = displayProfileRef.current;
      const effectiveScale = composeSpine38DisplayScale({
        normalizationScale: normalizationScaleRef.current,
        userScale: DEFAULT_USER_SCALE,
        displayModeScale: profile.scale,
      });
      const nextScaleX = effectiveScale;
      const nextScaleY = effectiveScale;
      if (slot.scale.x !== nextScaleX || slot.scale.y !== nextScaleY) {
        const centerBefore = {
          x: currentBounds.x + currentBounds.w / 2,
          y: currentBounds.y + currentBounds.h / 2,
        };
        slot.scale.set(nextScaleX, nextScaleY);
        const layoutBounds = layoutBoundsRef.current;
        const afterScale =
          layoutBounds
            ? toScreenRegion(slot, spine, layoutBounds)
            : boundsToRegion(slot.getBounds());
        if (afterScale) {
          slot.x += centerBefore.x - (afterScale.x + afterScale.w / 2);
          slot.y += centerBefore.y - (afterScale.y + afterScale.h / 2);
          currentBounds = translateRegion(
            afterScale,
            centerBefore.x - (afterScale.x + afterScale.w / 2),
            centerBefore.y - (afterScale.y + afterScale.h / 2),
          );
        }
      }

      if (
        lastAppliedPlacementKeyRef.current !== profile.placementKey &&
        profile.placementPoint
      ) {
        if (
          profile.edge &&
          !visualAnchorOffsetRef.current &&
          !alphaScanGivenUpRef.current
        ) {
          return currentBounds;
        }
        const scale = { x: slot.scale.x || 1, y: slot.scale.y || 1 };
        const anchorBounds = resolvePlacementAnchorRegion(
          currentBounds,
          actionBarAnchorOffsetRef.current,
          scale,
        );
        const anchorPoint = profile.edge
          ? resolvePlacementAnchorPoint(
              currentBounds,
              visualAnchorOffsetRef.current,
              scale,
            )
          : null;
        const delta = anchorPoint
          ? computePointDelta(anchorPoint, profile.placementPoint)
          : computeCenteringDelta(anchorBounds, profile.placementPoint);
        publishAnchorDiagnostics({
          placement: {
            layoutBounds: currentBounds,
            placementAnchor: anchorBounds,
            placementAnchorPoint: anchorPoint,
            placementPoint: profile.placementPoint,
            delta,
          },
        });
        if (delta) {
          slot.x += delta.dx;
          slot.y += delta.dy;
          currentBounds = translateRegion(currentBounds, delta.dx, delta.dy);
        }
      }
      lastAppliedPlacementKeyRef.current = profile.placementKey;
      return currentBounds;
    },
    [],
  );

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
    nextBounds = applyDisplayProfile(nextBounds);
    if (!isFiniteRect(nextBounds)) return;
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
      ? applyRegionOffset(nextBounds, actionBarAnchorOffset, {
          x: slot.scale.x || 1,
          y: slot.scale.y || 1,
        })
      : null;
    if (actionBarAnchor) {
      setActionBarAnchorIfChanged(actionBarAnchor);
    }
    publishAnchorDiagnostics({
      modelId: entry.id,
      backend: entry.backend,
      spriteScreen: nextBounds,
      visibleBounds: actionBarAnchor,
      actionBarAnchor: actionBarAnchor ?? nextBounds,
    });
    reportSpritePosition(nextBounds);
  }, [
    applyDisplayProfile,
    entry.backend,
    entry.id,
    setActionBarAnchorIfChanged,
    setSpriteScreen,
  ]);

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
      setActionBarAnchorIfChanged(null);
      layoutBoundsRef.current = null;
      lastSpriteScreenRef.current = null;
      lastActionBarAnchorRef.current = null;
      actionBarAnchorOffsetRef.current = null;
      visualAnchorOffsetRef.current = null;
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
        resolution: displayContextRef.current.dpr,
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

      slot.on("pointerdown", (event: unknown) => {
        const global = getGlobalPoint(event);
        if (!global) return;
        pointerStart = global;
        slotStart = { x: slot.x, y: slot.y };
        downTimestamp = performance.now();
        dragArmed = false;
      });

      slot.on("globalpointermove", (event: unknown) => {
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
      animationsRef.current = spine.skeleton.data.animations.map(
        (item: { name: string }) => item.name,
      );
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
      normalizationScaleRef.current = slot.scale.x;
      playMappedAction("idle");
      syncBounds();
      setReady(true);

      app.ticker.add((ticker: unknown) => {
        const currentApp = app;
        if (!currentApp) return;
        tickerElapsed += getTickerDeltaMs(ticker);
        if (tickerElapsed < 250) return;
        tickerElapsed = 0;
        if (!alphaScanReadyRef.current && !alphaScanGivenUpRef.current) {
          const layoutBounds = layoutBoundsRef.current;
          let screenBounds = layoutBounds
            ? toScreenRegion(slot, spine, layoutBounds)
            : boundsToRegion(slot.getBounds());
          const visibleRegion = screenBounds
            ? findAdaptiveVisibleRegionFromApp(currentApp, screenBounds)
            : null;
          let scanned = visibleRegion?.bounds ?? null;
          let visibleCentroid = visibleRegion?.centroid ?? null;
          if (scanned) {
            const localVisualBounds = toSpineLocalRegion(slot, spine, scanned);
            const localVisibleCentroid = visibleCentroid
              ? toSpineLocalPoint(slot, spine, visibleCentroid)
              : null;
            const centerBefore = {
              x: scanned.x + scanned.w / 2,
              y: scanned.y + scanned.h / 2,
            };
            const normalizationScale = resolveSpine38NormalizationScale(
              localVisualBounds,
              resolveInitialStageViewport(currentApp),
              entry.spine.fit,
            );
            normalizationScaleRef.current = normalizationScale;
            layoutBoundsRef.current = localVisualBounds;
            const effectiveScale = composeSpine38DisplayScale({
              normalizationScale,
              userScale: DEFAULT_USER_SCALE,
              displayModeScale: displayProfileRef.current.scale,
            });
            slot.scale.set(effectiveScale);
            let normalizedBounds = toScreenRegion(slot, spine, localVisualBounds);
            const normalizationDx =
              centerBefore.x - (normalizedBounds.x + normalizedBounds.w / 2);
            const normalizationDy =
              centerBefore.y - (normalizedBounds.y + normalizedBounds.h / 2);
            slot.x += normalizationDx;
            slot.y += normalizationDy;
            normalizedBounds = translateRegion(
              normalizedBounds,
              normalizationDx,
              normalizationDy,
            );
            scanned = normalizedBounds;
            screenBounds = normalizedBounds;
            visibleCentroid = localVisibleCentroid
              ? toScreenPoint(slot, spine, localVisibleCentroid)
              : null;
            lastSpriteScreenRef.current = null;
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
                visibleCentroid = visibleCentroid
                  ? { x: visibleCentroid.x + dx, y: visibleCentroid.y + dy }
                  : visibleCentroid;
                lastSpriteScreenRef.current = null;
              }
            }
            actionBarAnchorOffsetRef.current = screenBounds
              ? offsetRegionFromBase(scanned, screenBounds, {
                  x: slot.scale.x || 1,
                  y: slot.scale.y || 1,
                })
              : null;
            visualAnchorOffsetRef.current =
              screenBounds && visibleCentroid
                ? offsetPointFromBase(visibleCentroid, screenBounds, {
                    x: slot.scale.x || 1,
                    y: slot.scale.y || 1,
                  })
                : null;
            setActionBarAnchorIfChanged(scanned);
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
      setActionBarAnchorIfChanged(null);
      onRuntimeChange(null);
      layoutBoundsRef.current = null;
      lastSpriteScreenRef.current = null;
      lastActionBarAnchorRef.current = null;
      actionBarAnchorOffsetRef.current = null;
      visualAnchorOffsetRef.current = null;
      normalizationScaleRef.current = 1;
      lastAppliedPlacementKeyRef.current = null;
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
    setActionBarAnchorIfChanged,
    setSpriteScreen,
    syncBounds,
  ]);

  useEffect(() => {
    const app = appRef.current;
    if (!app || !syncPixiRendererDpr(app, displayContext.dpr)) return;

    alphaScanReadyRef.current = false;
    alphaScanGivenUpRef.current = false;
    actionBarAnchorOffsetRef.current = null;
    visualAnchorOffsetRef.current = null;
    lastSpriteScreenRef.current = null;
    lastActionBarAnchorRef.current = null;
    setActionBarAnchorIfChanged(null);
    syncBounds();
  }, [displayContext.dpr, setActionBarAnchorIfChanged, syncBounds]);

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
