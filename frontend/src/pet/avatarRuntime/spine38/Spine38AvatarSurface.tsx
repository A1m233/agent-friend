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
import type { PetFacing } from "@/pet/avatarRuntime/facing";
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import { isTauri } from "@/utils/tauri";
import type { PetDisplayProfile } from "@/pages/pet/petDisplayProfile";
import type { PetWebviewDisplayContext } from "@/pages/pet/petWebviewDisplayContext";
import { isFiniteRect } from "@/pages/pet/petGeometry";
import { syncPixiRendererDpr } from "@/pet/avatarRuntime/pixiRendererDpr";
import { isPrimaryPointerButton } from "@/pet/pointerInput";
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
  canSampleSpineVisibleBounds,
  createSpineVisibleBoundsStabilityState,
  discardPendingSpineVisibleBoundsSample,
  observeSpineVisibleBoundsSample,
  resetPendingSpineVisibleBoundsForDisplayChange,
} from "./spine38VisibleBoundsStability";
import {
  Spine38DragFacingDetector,
  orientPointOffset,
  orientRegionOffset,
  screenPointToSpineLocalPoint,
  screenRegionToSpineLocalRegion,
  slotRegionToScreenRegion,
  spineLocalPointToScreenPoint,
  spineLocalRegionToSlotRegion,
} from "./spine38Facing";
import { Spine38TurnTransition } from "./spine38TurnTransition";
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
  facing: PetFacing;
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setActionBarAnchor: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setIsDragging: (v: boolean) => void;
  onFacingChange: (facing: PetFacing) => void;
  onRuntimeChange: (runtime: PetAvatarRuntime | null) => void;
}

const DRAG_MOVE_THRESHOLD_PX = 5;
const CLICK_MAX_DURATION_MS = 300;
const ALPHA_SCAN_NULL_FRAMES = 30;
// Future settings slider plugs into this factor without changing model normalization or mini mode.

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
  facing: PetFacing,
): PixelRegion {
  return slotRegionToScreenRegion(
    { x: slot.x, y: slot.y },
    { x: slot.scale.x, y: slot.scale.y },
    spineLocalRegionToSlotRegion({ x: spine.x, y: spine.y }, bounds, facing),
  );
}

function toSlotHitRegion(
  spine: Spine38,
  bounds: PixelRegion,
  facing: PetFacing,
): PixelRegion {
  return spineLocalRegionToSlotRegion({ x: spine.x, y: spine.y }, bounds, facing);
}

function toSpineLocalRegion(
  slot: PIXI7.Container,
  spine: Spine38,
  bounds: PixelRegion,
  facing: PetFacing,
): PixelRegion {
  return screenRegionToSpineLocalRegion(
    { x: slot.x, y: slot.y },
    { x: slot.scale.x, y: slot.scale.y },
    { x: spine.x, y: spine.y },
    bounds,
    facing,
  );
}

function toSpineLocalPoint(
  slot: PIXI7.Container,
  spine: Spine38,
  point: PixelPoint,
  facing: PetFacing,
): PixelPoint {
  return screenPointToSpineLocalPoint(
    { x: slot.x, y: slot.y },
    { x: slot.scale.x, y: slot.scale.y },
    { x: spine.x, y: spine.y },
    point,
    facing,
  );
}

function toScreenPoint(
  slot: PIXI7.Container,
  spine: Spine38,
  point: PixelPoint,
  facing: PetFacing,
): PixelPoint {
  return spineLocalPointToScreenPoint(
    { x: slot.x, y: slot.y },
    { x: slot.scale.x, y: slot.scale.y },
    { x: spine.x, y: spine.y },
    point,
    facing,
  );
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
  facing,
  setSpriteScreen,
  setActionBarAnchor,
  setIsDragging,
  onFacingChange,
  onRuntimeChange,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const appRef = useRef<PIXI7.Application<HTMLCanvasElement> | null>(null);
  const slotRef = useRef<PIXI7.Container | null>(null);
  const facingLayerRef = useRef<PIXI7.Container | null>(null);
  const spineRef = useRef<Spine38 | null>(null);
  const facingRef = useRef(facing);
  const geometryFacingRef = useRef(facing);
  const turnTransitionRef = useRef<Spine38TurnTransition | null>(null);
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
  const visibleBoundsStabilityRef = useRef(
    createSpineVisibleBoundsStabilityState(performance.now()),
  );
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
  facingRef.current = facing;

  const applyDisplayProfile = useCallback(
    (currentBounds: PixelRegion): PixelRegion => {
      const slot = slotRef.current;
      const spine = spineRef.current;
      if (!slot || !spine) return currentBounds;

      const profile = displayProfileRef.current;
      const effectiveScale = composeSpine38DisplayScale({
        normalizationScale: normalizationScaleRef.current,
        userScale: profile.modelScale,
        displayModeScale: profile.modeScale,
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
            ? toScreenRegion(slot, spine, layoutBounds, geometryFacingRef.current)
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
          actionBarAnchorOffsetRef.current
            ? orientRegionOffset(
                actionBarAnchorOffsetRef.current,
                layoutBoundsRef.current?.w ?? currentBounds.w / scale.x,
                geometryFacingRef.current,
              )
            : null,
          scale,
        );
        const anchorPoint = profile.edge
          ? resolvePlacementAnchorPoint(
              currentBounds,
              visualAnchorOffsetRef.current
                ? orientPointOffset(
                    visualAnchorOffsetRef.current,
                    layoutBoundsRef.current?.w ?? currentBounds.w / scale.x,
                    geometryFacingRef.current,
                  )
                : null,
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
      ? toScreenRegion(slot, spine, layoutBounds, geometryFacingRef.current)
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
        ? toSlotHitRegion(spine, layoutBounds, geometryFacingRef.current)
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
      ? applyRegionOffset(
          nextBounds,
          orientRegionOffset(
            actionBarAnchorOffset,
            layoutBounds?.w ?? nextBounds.w / (slot.scale.x || 1),
            geometryFacingRef.current,
          ),
          {
          x: slot.scale.x || 1,
          y: slot.scale.y || 1,
          },
        )
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
      visibleBoundsStabilityRef.current = createSpineVisibleBoundsStabilityState(
        performance.now(),
      );
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

      const facingLayer = new PIXI7.Container();
      const turnTransition = new Spine38TurnTransition(facingRef.current);
      facingLayer.scale.set(turnTransition.scaleX, 1);
      slot.addChild(facingLayer);
      facingLayerRef.current = facingLayer;
      turnTransitionRef.current = turnTransition;
      geometryFacingRef.current = facingRef.current;
      const facingDetector = new Spine38DragFacingDetector(facingRef.current);

      let pointerStart: { x: number; y: number } | null = null;
      let slotStart: { x: number; y: number } | null = null;
      let downTimestamp = 0;
      let dragArmed = false;

      slot.on("pointerdown", (event: unknown) => {
        if (!isPrimaryPointerButton(event)) return;
        const global = getGlobalPoint(event);
        if (!global) return;
        pointerStart = global;
        slotStart = { x: slot.x, y: slot.y };
        downTimestamp = performance.now();
        dragArmed = false;
        facingDetector.begin(global, facingRef.current);
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
        const nextFacing = facingDetector.move(global);
        if (nextFacing && turnTransition.turnTo(nextFacing)) {
          onFacingChange(nextFacing);
        }
        slot.x = slotStart.x + dx;
        slot.y = slotStart.y + dy;
        syncBounds();
      });

      const endPointer = (event: unknown) => {
        if (!pointerStart || !slotStart) return;
        if (!isPrimaryPointerButton(event)) return;
        const global = getGlobalPoint(event);
        const duration = performance.now() - downTimestamp;
        const dx = global ? global.x - pointerStart.x : 0;
        const dy = global ? global.y - pointerStart.y : 0;
        const dist = Math.hypot(dx, dy);
        const wasClick =
          !dragArmed && duration < CLICK_MAX_DURATION_MS && dist < DRAG_MOVE_THRESHOLD_PX;

        pointerStart = null;
        slotStart = null;
        facingDetector.end();

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
      facingLayer.addChild(spine);
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
        const deltaMs = getTickerDeltaMs(ticker);
        const turnFrame = turnTransition.update(deltaMs);
        facingLayer.scale.x = turnFrame.scaleX;
        if (turnFrame.completedFacing) {
          geometryFacingRef.current = turnFrame.completedFacing;
          lastSpriteScreenRef.current = null;
          syncBounds();
        }
        tickerElapsed += deltaMs;
        if (tickerElapsed < 250) return;
        tickerElapsed = 0;
        if (
          !turnTransition.isAnimating &&
          !alphaScanReadyRef.current &&
          !alphaScanGivenUpRef.current
        ) {
          const sampleTime = performance.now();
          if (
            !canSampleSpineVisibleBounds(
              visibleBoundsStabilityRef.current,
              sampleTime,
            )
          ) {
            syncBounds();
            return;
          }
          const layoutBounds = layoutBoundsRef.current;
          let screenBounds = layoutBounds
            ? toScreenRegion(slot, spine, layoutBounds, geometryFacingRef.current)
            : boundsToRegion(slot.getBounds());
          const visibleRegion = screenBounds
            ? findAdaptiveVisibleRegionFromApp(currentApp, screenBounds)
            : null;
          let scanned = visibleRegion?.bounds ?? null;
          let visibleCentroid = visibleRegion?.centroid ?? null;
          if (scanned) {
            const localVisualBounds = toSpineLocalRegion(
              slot,
              spine,
              scanned,
              geometryFacingRef.current,
            );
            visibleBoundsStabilityRef.current = observeSpineVisibleBoundsSample(
              visibleBoundsStabilityRef.current,
              localVisualBounds,
              sampleTime,
            );
            if (!visibleBoundsStabilityRef.current.accepted) {
              nullScanCount = 0;
              syncBounds();
              return;
            }
            const localVisibleCentroid = visibleCentroid
              ? toSpineLocalPoint(
                  slot,
                  spine,
                  visibleCentroid,
                  geometryFacingRef.current,
                )
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
              userScale: displayProfileRef.current.modelScale,
              displayModeScale: displayProfileRef.current.modeScale,
            });
            slot.scale.set(effectiveScale);
            let normalizedBounds = toScreenRegion(
              slot,
              spine,
              localVisualBounds,
              geometryFacingRef.current,
            );
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
              ? toScreenPoint(
                  slot,
                  spine,
                  localVisibleCentroid,
                  geometryFacingRef.current,
                )
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
            visibleBoundsStabilityRef.current =
              discardPendingSpineVisibleBoundsSample(
                visibleBoundsStabilityRef.current,
              );
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
      visibleBoundsStabilityRef.current = createSpineVisibleBoundsStabilityState(
        performance.now(),
      );
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
      facingLayerRef.current = null;
      spineRef.current = null;
      turnTransitionRef.current = null;
    };
  }, [
    entry,
    onFacingChange,
    onRuntimeChange,
    playMappedAction,
    setIsDragging,
    setActionBarAnchorIfChanged,
    setSpriteScreen,
    syncBounds,
  ]);

  useEffect(() => {
    const transition = turnTransitionRef.current;
    const facingLayer = facingLayerRef.current;
    if (!transition || !facingLayer || transition.targetFacing === facing) return;
    const frame = transition.snap(facing);
    facingLayer.scale.x = frame.scaleX;
    geometryFacingRef.current = facing;
    lastSpriteScreenRef.current = null;
    syncBounds();
  }, [facing, syncBounds]);

  useEffect(() => {
    visibleBoundsStabilityRef.current =
      resetPendingSpineVisibleBoundsForDisplayChange(
        visibleBoundsStabilityRef.current,
        performance.now(),
      );
  }, [
    displayContext.dpr,
    displayContext.overlayViewport.h,
    displayContext.overlayViewport.w,
    displayContext.topologyGeneration,
  ]);

  useEffect(() => {
    const app = appRef.current;
    if (!app || !syncPixiRendererDpr(app, displayContext.dpr)) return;

    // DPR changes alter framebuffer density, not the Spine model's local geometry. Keep the
    // last trusted visible bounds and normalization scale instead of re-learning them from a
    // resize-transition frame, which can contain only a fragment of the avatar.
    lastSpriteScreenRef.current = null;
    lastActionBarAnchorRef.current = null;
    syncBounds();
  }, [displayContext.dpr, syncBounds]);

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
