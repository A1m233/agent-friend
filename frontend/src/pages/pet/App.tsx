import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEventHandler,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Menu } from "@tauri-apps/api/menu";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isTauri } from "@/utils/tauri";
import {
  openFeedback as openFeedbackWindow,
  openSettings as openSettingsWindow,
} from "@/lib/settings/navigation";
import type { PushEnvelope } from "@/types/push";
import { useSpine38ImportCoordinator } from "@/avatar-models";

import { ActionBar } from "./ActionBar";
import { shouldRenderActionBar } from "./actionBarVisibility";
import { runNativeContextMenuPopup } from "./nativeContextMenuLifecycle";
import { usePetPassthrough } from "./usePetPassthrough";
import {
  resolvePetDisplayProfile,
  type PetPoint,
  type PetRect,
  type PetViewport,
} from "./petDisplayProfile";
import {
  readWebviewDevicePixelRatio,
  type PetWebviewDisplayContext,
} from "./petWebviewDisplayContext";
import {
  getPetOverlayViewport,
  loadPetDisplayPhysicalSnapshot,
  loadPetInitialPlacementPoint,
  loadPetMonitorViewports,
  rebasePetPointByMonitorAnchorWithDiagnostics,
  resolvePetMonitorRelativeAnchorForPoint,
  resolvePetPointFromMonitorRelativeAnchor,
  resolvePetViewportForRect,
  type MonitorPhysicalSnapshot,
  type OverlayPhysicalBounds,
  type PetMonitorRelativeAnchor,
} from "./petMonitorViewport";
import {
  loadPetUiPersistence,
  savePetFacing,
  savePetMonitorAnchor,
} from "./petUiPersistence";
import {
  centerPointOfRect,
  hasReachedPlacementPoint,
  isFiniteRect,
  isFiniteViewport,
  samePoint,
  sameRect,
  sameViewportList,
} from "./petGeometry";
import {
  nextPetInitialPlacementSettled,
  shouldSchedulePetAnchorPersistence,
} from "./petPlacementPersistence";
import type { PetAvatarSize } from "@/lib/settings";
import {
  resolveInstalledAvatarModelSelection,
  useInstalledAvatarModelCatalog,
} from "@/avatar-models";
import { getAvatarModelScalePercent, useSetting } from "@/lib/settings";
import { Live2DAvatarSurface } from "@/pet/avatarRuntime/live2d/Live2DAvatarSurface";
import { Spine38AvatarSurface } from "@/pet/avatarRuntime/spine38/Spine38AvatarSurface";
import type { PetAvatarRuntime } from "@/pet/avatarRuntime/types";
import {
  DEFAULT_PET_FACING,
  normalizePetFacing,
  type PetFacing,
} from "@/pet/avatarRuntime/facing";
import { publishAnchorDiagnostics } from "@/pet/anchorDiagnostics";
import {
  startPetStateSubscriber,
  usePetStateStore,
  type PetPhase,
} from "@/stores/petState";
import {
  focusVoiceCallWindow,
  useVoiceStore,
} from "@/stores/voice";
import { isVoiceCallLive } from "@/stores/voiceStateMachine";

/**
 * Tauri listener stale-cleanup race 兜底：StrictMode 双 mount / hot reload 期间 Tauri 内部
 * listeners table 已被新进程清空，旧 unlisten 调用会抛 `listeners[eventId].handlerId undefined`。
 * 功能不影响（监听本就断了），但 console 噪音多。统一吞掉。
 */
function safeUnlisten(fn: (() => void) | null | undefined): void {
  if (!fn) return;
  try {
    fn();
  } catch {
    /* stale-cleanup race ignored */
  }
}

const PET_DISPLAY_TOPOLOGY_CHANGED_EVENT = "pet://display-topology-changed";
const PET_INITIAL_PLACEMENT_SETTLE_MS = 150;
interface PetDisplayTopologyChangedPayload {
  reason: string;
  generation: number;
  bounds: OverlayPhysicalBounds;
  previousBounds?: OverlayPhysicalBounds | null;
  previousMonitors?: MonitorPhysicalSnapshot[] | null;
  monitors: MonitorPhysicalSnapshot[];
}

interface PendingTopologyAnchor {
  generation: number;
  bounds: OverlayPhysicalBounds;
  monitors: MonitorPhysicalSnapshot[];
  anchor: PetMonitorRelativeAnchor;
}

interface AvatarPlacementIntent {
  generation: number;
  point: PetPoint;
}

function displayTopologySignature(payload: Pick<
  PetDisplayTopologyChangedPayload,
  "bounds" | "monitors"
>): string {
  return JSON.stringify({
    bounds: payload.bounds,
    monitors: payload.monitors,
  });
}

/**
 * 桌宠窗（18 · Live2D 形象 + 状态机 + Codex 兼容 + lip-sync）。
 *
 * 形态承接 17a 整屏 transparent overlay 底座 + PIXI canvas + avatar-slot Container；
 * 17b 在 slot 内填 Hiyori Live2DSprite + 挂 4 态状态机（idle/thinking/speaking/error）
 * + 文本 cadence lip-sync + Codex push event 驱动。
 *
 * 17a 接缝点 #2 sprite world position 数据流 / #3 cursor alpha hit-test / #5 操作栏
 * hover bridge 全部保持不动（design §3.6）。
 *
 * 详见 [docs/requirements/018-pet-live2d-state-and-lipsync/](../../../../docs/requirements/018-pet-live2d-state-and-lipsync/)。
 */
export function PetApp() {
  useSpine38ImportCoordinator();
  const [spriteScreen, setSpriteScreen] = useState<PetRect | null>(null);
  const [actionBarAnchor, setActionBarAnchor] = useState<PetRect | null>(null);
  const [avatarPlacementPoint, setAvatarPlacementPoint] = useState<PetPoint | null>(null);
  const [placementIntent, setAvatarPlacementIntent] =
    useState<AvatarPlacementIntent | null>(null);
  const [initialPlacementSettled, setInitialPlacementSettled] = useState(
    () => !isTauri(),
  );
  const spriteScreenRef = useRef<PetRect | null>(null);
  const userMovedPetRef = useRef(false);
  const placementIntentGenerationRef = useRef(0);
  const pendingTopologyAnchorRef = useRef<PendingTopologyAnchor | null>(null);
  const lastTopologyGenerationRef = useRef(0);
  const lastTopologySignatureRef = useRef<string | null>(null);
  const [webviewDpr, setWebviewDpr] = useState(readWebviewDevicePixelRatio);
  const [displayTopologyGeneration, setDisplayTopologyGeneration] = useState(0);
  // AC-6 hover gate · 双源：cursorOnSprite（Rust 60Hz cursor channel 驱动）
  //                + hoverActionBarDom（ActionBar 自身 onMouseEnter/Leave 驱动）
  // 单一 hoverActionBar 不够：cursor 离开 sprite 后切回原生鼠标穿透，
  // PIXI 不再收事件、pointerout 不触发；改由 usePetPassthrough 拿 Rust cursor 数据每帧判断。
  const [cursorOnSprite, setCursorOnSprite] = useState(false);
  const [hoverActionBarDom, setHoverActionBarDom] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [nativeContextMenuOpen, setNativeContextMenuOpen] = useState(false);
  const nativeContextMenuRef = useRef<Promise<Menu> | null>(null);
  const nativeContextMenuPopupInFlightRef = useRef(false);
  // 18 · dev-only 切穿透：开 inspector 调试时点"穿透 OFF"让 pet 变实窗，inspector 可点
  const [passthroughDisabled, setPassthroughDisabled] = useState(false);
  const voicePhase = useVoiceStore((s) => s.phase);
  const requestStartVoice = useVoiceStore((s) => s.requestStartFromAnyWindow);
  const [runtime, setRuntime] = useState<PetAvatarRuntime | null>(null);
  const [petFacing, setPetFacing] = useState<PetFacing | null>(() =>
    isTauri() ? null : DEFAULT_PET_FACING,
  );
  const petFacingRef = useRef<PetFacing | null>(petFacing);
  const [avatarModelId] = useSetting("avatarModelId");
  const [petAvatarSize, setPetAvatarSize] = useSetting("petAvatarSize");
  const [avatarModelScalePercents] = useSetting("avatarModelScalePercents");
  const [petMiniScalePercent] = useSetting("petMiniScalePercent");
  const [scalePreview, setScalePreview] = useState<{
    modelId?: string;
    modelScalePercent?: number;
    miniScalePercent?: number;
  } | null>(null);
  const previousPetAvatarSizeRef = useRef<PetAvatarSize | null>(null);
  const [overlayViewport, setOverlayViewport] = useState<PetViewport>(() =>
    getPetOverlayViewport(),
  );
  const topologyOverlayViewportRef = useRef<PetViewport>(overlayViewport);
  const [monitorViewports, setMonitorViewports] = useState<PetViewport[]>([]);
  const {
    entries: installedAvatarModels,
    error: installedAvatarModelError,
  } = useInstalledAvatarModelCatalog();
  const selectedEntry = resolveInstalledAvatarModelSelection(
    installedAvatarModels,
    avatarModelId,
  );

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    void loadPetUiPersistence()
      .then((state) => {
        if (!cancelled) {
          const restoredFacing = normalizePetFacing(state.facing);
          petFacingRef.current = restoredFacing;
          setPetFacing(restoredFacing);
        }
      })
      .catch((error: unknown) => {
        console.warn("[pet] load facing failed:", error);
        if (!cancelled) {
          petFacingRef.current = DEFAULT_PET_FACING;
          setPetFacing(DEFAULT_PET_FACING);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleFacingChange = useCallback((nextFacing: PetFacing) => {
    if (petFacingRef.current === nextFacing) return;
    petFacingRef.current = nextFacing;
    setPetFacing(nextFacing);
    void savePetFacing(nextFacing).catch((error: unknown) => {
      console.warn("[pet] save facing failed:", error);
    });
  }, []);

  useEffect(() => {
    if (!isTauri()) return;
    const unlistenPromise = listen<{
      modelId?: string;
      modelScalePercent?: number;
      miniScalePercent?: number;
    }>("avatar-models://scale-preview", (event) => {
      setScalePreview((current) => ({ ...current, ...event.payload }));
    });
    return () => void unlistenPromise.then((unlisten) => unlisten());
  }, []);

  useEffect(() => {
    spriteScreenRef.current = spriteScreen;
  }, [spriteScreen]);

  const setSpriteScreenIfChanged = useCallback((next: PetRect | null) => {
    if (next && !isFiniteRect(next)) return;
    setSpriteScreen((prev) => (sameRect(prev, next) ? prev : next));
  }, []);

  const setActionBarAnchorIfChanged = useCallback((next: PetRect | null) => {
    if (next && !isFiniteRect(next)) return;
    setActionBarAnchor((prev) => (sameRect(prev, next) ? prev : next));
  }, []);

  const markPetPositionUserOwned = useCallback(() => {
    userMovedPetRef.current = true;
    setInitialPlacementSettled((current) =>
      nextPetInitialPlacementSettled(current, { type: "user-owned" }),
    );
  }, []);

  const setIsDraggingWithTracking = useCallback(
    (next: boolean) => {
      if (next) {
        markPetPositionUserOwned();
      }
      setIsDragging(next);
    },
    [markPetPositionUserOwned],
  );

  useEffect(() => {
    let cancelled = false;
    let dprQuery: MediaQueryList | null = null;
    let topologyUnlisten: (() => void) | null = null;
    let resizedUnlisten: (() => void) | null = null;
    let scaleUnlisten: (() => void) | null = null;
    let viewportRequestId = 0;
    let deferredAnchorTimer: number | null = null;
    let initialPlacementTimer: number | null = null;
    let initialPlacementRequested = false;
    const syncWebviewDpr = () => {
      const nextDpr = readWebviewDevicePixelRatio();
      setWebviewDpr((prev) =>
        Math.abs(prev - nextDpr) < 0.001 ? prev : nextDpr,
      );
    };
    const applyPendingTopologyAnchor = (overlaySize: PetViewport): boolean => {
      const pending = pendingTopologyAnchorRef.current;
      if (!pending || !isFiniteViewport(overlaySize)) return false;

      const point = resolvePetPointFromMonitorRelativeAnchor({
        anchor: pending.anchor,
        bounds: pending.bounds,
        monitors: pending.monitors,
        overlaySize: { w: overlaySize.w, h: overlaySize.h },
      });
      if (!point) return false;

      pendingTopologyAnchorRef.current = null;
      topologyOverlayViewportRef.current = overlaySize;
      setAvatarPlacementPoint((prev) => (samePoint(prev, point) ? prev : point));
      setAvatarPlacementIntent((prev) =>
        prev?.generation === pending.generation && samePoint(prev.point, point)
          ? prev
          : { generation: pending.generation, point },
      );
      return true;
    };
    const scheduleDeferredAnchorApply = () => {
      if (deferredAnchorTimer !== null) {
        window.clearTimeout(deferredAnchorTimer);
      }
      deferredAnchorTimer = window.setTimeout(() => {
        deferredAnchorTimer = null;
        updateViewport("topology-anchor-deferred");
      }, 250);
    };
    const resolveInitialPlacement = (overlaySize: PetViewport) => {
      if (initialPlacementRequested) return;
      initialPlacementRequested = true;
      void loadPetInitialPlacementPoint(overlaySize)
        .then((point) => {
          if (cancelled) return;
          const currentOverlaySize = getPetOverlayViewport();
          if (!sameRect(overlaySize, currentOverlaySize)) {
            initialPlacementRequested = false;
            scheduleInitialPlacement(currentOverlaySize, "result-size-changed");
            return;
          }
          const userMoved = userMovedPetRef.current;
          if (point && !userMoved) {
            setAvatarPlacementPoint((prev) => prev ?? point);
            const generation = --placementIntentGenerationRef.current;
            setAvatarPlacementIntent((prev) =>
              prev?.generation === generation && samePoint(prev.point, point)
                ? prev
                : { generation, point },
            );
          }
          setInitialPlacementSettled((current) =>
            nextPetInitialPlacementSettled(current, {
              type: "resolved",
              hasPoint: point !== null,
              userMoved,
            }),
          );
        })
        .catch((error: unknown) => {
          if (!cancelled) {
            console.warn("[pet] resolve initial placement failed:", error);
            // Keep the persistence gate closed: saving the renderer's startup position here
            // would overwrite the last valid anchor. A later user drag explicitly unlocks it.
            setInitialPlacementSettled((current) =>
              nextPetInitialPlacementSettled(current, { type: "failed" }),
            );
          }
        });
    };
    const scheduleInitialPlacement = (
      _overlaySize: PetViewport,
      _reason: string,
    ) => {
      if (initialPlacementRequested) return;
      if (initialPlacementTimer !== null) {
        window.clearTimeout(initialPlacementTimer);
      }
      initialPlacementTimer = window.setTimeout(() => {
        initialPlacementTimer = null;
        resolveInitialPlacement(getPetOverlayViewport());
      }, PET_INITIAL_PLACEMENT_SETTLE_MS);
    };
    const updateViewport = (reason = "unknown") => {
      const requestId = ++viewportRequestId;
      syncWebviewDpr();
      const nextOverlayViewport = getPetOverlayViewport();
      if (!isFiniteViewport(nextOverlayViewport)) return;
      scheduleInitialPlacement(nextOverlayViewport, reason);
      setOverlayViewport((prev) =>
        sameRect(prev, nextOverlayViewport) ? prev : nextOverlayViewport,
      );
      void loadPetMonitorViewports(nextOverlayViewport)
        .then((nextMonitorViewports) => {
          if (cancelled || requestId !== viewportRequestId) return;
          const finiteMonitorViewports = nextMonitorViewports.filter(isFiniteViewport);
          if (reason !== "topology-event") {
            const hadPendingTopologyAnchor = pendingTopologyAnchorRef.current !== null;
            const appliedPendingTopologyAnchor =
              applyPendingTopologyAnchor(nextOverlayViewport);
            if (!hadPendingTopologyAnchor || appliedPendingTopologyAnchor) {
              topologyOverlayViewportRef.current = nextOverlayViewport;
            }
          }
          setMonitorViewports((prev) =>
            sameViewportList(prev, finiteMonitorViewports)
              ? prev
              : finiteMonitorViewports,
          );
        })
        .catch((error: unknown) => {
          if (!cancelled && requestId === viewportRequestId) {
            setMonitorViewports((prev) => (prev.length === 0 ? prev : []));
          }
          console.warn("[pet] load monitor viewports failed:", error);
        });
      bindDprListener();
    };
    const onDprChange = () => updateViewport("dpr-media-query");
    const bindDprListener = () => {
      if (typeof window.matchMedia !== "function") return;
      dprQuery?.removeEventListener("change", onDprChange);
      dprQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      dprQuery.addEventListener("change", onDprChange);
    };
    updateViewport("effect-init");
    const onWindowResize = () => updateViewport("window-resize");
    window.addEventListener("resize", onWindowResize);
    const handleTopologyChanged = (event: { payload: PetDisplayTopologyChangedPayload }) => {
      const signature = displayTopologySignature(event.payload);
      if (
        event.payload.generation <= lastTopologyGenerationRef.current ||
        signature === lastTopologySignatureRef.current
      ) {
        return;
      }
      lastTopologyGenerationRef.current = event.payload.generation;
      lastTopologySignatureRef.current = signature;
      setDisplayTopologyGeneration(event.payload.generation);

      const previousBounds = event.payload.previousBounds;
      const previousMonitors = event.payload.previousMonitors;
      const nextMonitors = event.payload.monitors;
      const currentSpriteScreen = spriteScreenRef.current;
      const previousOverlayViewport = topologyOverlayViewportRef.current;
      const nextOverlayViewport = getPetOverlayViewport();
      if (
        previousBounds &&
        previousMonitors?.length &&
        nextMonitors.length &&
        isFiniteRect(currentSpriteScreen) &&
        isFiniteViewport(previousOverlayViewport) &&
        isFiniteViewport(nextOverlayViewport)
      ) {
        const result = rebasePetPointByMonitorAnchorWithDiagnostics({
          point: centerPointOfRect(currentSpriteScreen),
          previousBounds,
          nextBounds: event.payload.bounds,
          previousMonitors,
          nextMonitors,
          previousOverlaySize: {
            w: previousOverlayViewport.w,
            h: previousOverlayViewport.h,
          },
          nextOverlaySize: {
            w: nextOverlayViewport.w,
            h: nextOverlayViewport.h,
          },
        });
        if (
          result.diagnostics.nextViewport &&
          result.diagnostics.relativeAnchor
        ) {
          pendingTopologyAnchorRef.current = {
            generation: event.payload.generation,
            bounds: event.payload.bounds,
            monitors: nextMonitors,
            anchor: {
              monitor: result.diagnostics.nextViewport.monitor,
              relativeAnchor: result.diagnostics.relativeAnchor,
            },
          };
        }
      }
      updateViewport("topology-event");
      if (pendingTopologyAnchorRef.current) {
        scheduleDeferredAnchorApply();
      } else {
        topologyOverlayViewportRef.current = nextOverlayViewport;
      }
    };
    if (isTauri()) {
      void listen<PetDisplayTopologyChangedPayload>(
        PET_DISPLAY_TOPOLOGY_CHANGED_EVENT,
        handleTopologyChanged,
      ).then((u) => {
        if (cancelled) safeUnlisten(u);
        else topologyUnlisten = u;
      });
      const currentWindow = getCurrentWindow();
      void currentWindow.onResized(() => updateViewport("tauri-resized")).then((u) => {
        if (cancelled) safeUnlisten(u);
        else resizedUnlisten = u;
      });
      void currentWindow.onScaleChanged(() => updateViewport("tauri-scale-changed")).then((u) => {
        if (cancelled) safeUnlisten(u);
        else scaleUnlisten = u;
      });
    }
    return () => {
      cancelled = true;
      window.removeEventListener("resize", onWindowResize);
      dprQuery?.removeEventListener("change", onDprChange);
      if (deferredAnchorTimer !== null) {
        window.clearTimeout(deferredAnchorTimer);
      }
      if (initialPlacementTimer !== null) {
        window.clearTimeout(initialPlacementTimer);
      }
      safeUnlisten(topologyUnlisten);
      safeUnlisten(resizedUnlisten);
      safeUnlisten(scaleUnlisten);
    };
  }, []);

  const interactionViewport = useMemo(
    () =>
      resolvePetViewportForRect(
        actionBarAnchor ?? spriteScreen,
        monitorViewports,
        overlayViewport,
      ),
    [actionBarAnchor, monitorViewports, overlayViewport, spriteScreen],
  );

  const displayProfile = useMemo(
    () => {
      const anchor = actionBarAnchor ?? spriteScreen;
      const baseProfile = resolvePetDisplayProfile({
        avatarSize: petAvatarSize,
        rect: anchor,
        viewport: interactionViewport,
        previousAvatarSize: previousPetAvatarSizeRef.current,
        modelScale:
          (scalePreview?.modelId === avatarModelId && scalePreview.modelScalePercent !== undefined
            ? scalePreview.modelScalePercent
            : getAvatarModelScalePercent(avatarModelScalePercents, avatarModelId)) / 100,
        miniScale: (scalePreview?.miniScalePercent ?? petMiniScalePercent) / 100,
      });
      if (!placementIntent) return baseProfile;
      return {
        ...baseProfile,
        placementKey: `${baseProfile.placementKey}:placement-${placementIntent.generation}`,
        placementPoint: placementIntent.point,
        edge: null,
      };
    },
    [
      actionBarAnchor,
      interactionViewport,
      avatarModelId,
      avatarModelScalePercents,
      petAvatarSize,
      petMiniScalePercent,
      scalePreview,
      spriteScreen,
      placementIntent,
    ],
  );

  useEffect(() => {
    previousPetAvatarSizeRef.current = petAvatarSize;
  }, [petAvatarSize]);

  useEffect(() => {
    if (!placementIntent) return;
    if (
      hasReachedPlacementPoint(
        placementIntent.point,
        actionBarAnchor,
        spriteScreen,
      )
    ) {
      setAvatarPlacementIntent(null);
    }
  }, [actionBarAnchor, spriteScreen, placementIntent]);

  useEffect(() => {
    setRuntime(null);
    setSpriteScreenIfChanged(null);
    setActionBarAnchorIfChanged(null);
    publishAnchorDiagnostics({
      modelId: selectedEntry?.id ?? null,
      backend: selectedEntry?.backend ?? null,
      spriteScreen: null,
      visibleBounds: null,
      actionBarAnchor: null,
      actionBarComputed: null,
      actionBarRect: null,
    });
  }, [
    selectedEntry?.backend,
    selectedEntry?.id,
    setActionBarAnchorIfChanged,
    setSpriteScreenIfChanged,
  ]);

  useEffect(() => {
    if (!installedAvatarModelError) return;
    console.warn("[pet] installed avatar model catalog failed:", installedAvatarModelError);
  }, [installedAvatarModelError]);

  useEffect(() => {
    publishAnchorDiagnostics({
      modelId: selectedEntry?.id ?? null,
      backend: selectedEntry?.backend ?? null,
      spriteScreen,
      actionBarAnchor: actionBarAnchor ?? spriteScreen,
      dpr: webviewDpr,
      overlayViewport,
      selectedViewport: interactionViewport,
      monitorViewports,
      displayProfile: {
        avatarSize: displayProfile.avatarSize,
        placementKey: displayProfile.placementKey,
        modelScale: displayProfile.modelScale,
        modeScale: displayProfile.modeScale,
        renderScale: displayProfile.renderScale,
        placementPoint: displayProfile.placementPoint,
        edge: displayProfile.edge,
      },
    });
  }, [
    actionBarAnchor,
    displayProfile,
    interactionViewport,
    monitorViewports,
    overlayViewport,
    selectedEntry?.backend,
    selectedEntry?.id,
    spriteScreen,
    webviewDpr,
  ]);

  useEffect(() => {
    const placementRect = actionBarAnchor ?? spriteScreen;
    if (!isFiniteRect(placementRect)) return;
    const nextPlacementPoint = centerPointOfRect(placementRect);
    setAvatarPlacementPoint((prev) =>
      samePoint(prev, nextPlacementPoint) ? prev : nextPlacementPoint,
    );
  }, [actionBarAnchor, spriteScreen]);

  useEffect(() => {
    const persistenceRect = actionBarAnchor ?? spriteScreen;
    if (!shouldSchedulePetAnchorPersistence({
      isTauri: isTauri(),
      initialPlacementSettled,
      isDragging,
      hasPlacementIntent: placementIntent !== null,
    }) ||
      !isFiniteRect(persistenceRect) ||
      !isFiniteViewport(overlayViewport)
    ) {
      return;
    }

    const point = centerPointOfRect(persistenceRect);
    const timer = window.setTimeout(() => {
      void loadPetDisplayPhysicalSnapshot()
        .then((snapshot) => {
          if (!snapshot) return;
          const anchor = resolvePetMonitorRelativeAnchorForPoint({
            point,
            bounds: snapshot.bounds,
            monitors: snapshot.monitors,
            overlaySize: { w: overlayViewport.w, h: overlayViewport.h },
          });
          if (!anchor) return;
          void savePetMonitorAnchor(anchor)
            .catch((error: unknown) => {
              console.warn("[pet] save monitor anchor failed:", error);
            });
        })
        .catch((error: unknown) => {
          console.warn("[pet] resolve monitor anchor failed:", error);
        });
    }, 500);

    return () => {
      window.clearTimeout(timer);
    };
  }, [
    actionBarAnchor,
    isDragging,
    initialPlacementSettled,
    overlayViewport,
    spriteScreen,
    placementIntent,
  ]);

  useEffect(() => {
    if (!runtime) return;
    runtime.setPhase(usePetStateStore.getState().phase);
    const unsub = usePetStateStore.subscribe((state) => {
      runtime.setPhase(state.phase);
    });
    return unsub;
  }, [runtime]);

  // 17a · cursor passthrough（DOM data-hit 优先 + 18 alpha hit-test 主路径 + spriteScreen 矩形兜底 + isDragging 互锁）
  // + AC-6 cursorOnSprite 驱动 + 18 dev-only disabled 切换
  usePetPassthrough({
    isDragging,
    spriteScreen,
    alphaHitTest: runtime?.alphaHitTest ?? null,
    setCursorOnSprite,
    disabled: passthroughDisabled,
  });

  const displayContext = useMemo<PetWebviewDisplayContext>(
    () => ({
      dpr: webviewDpr,
      overlayViewport,
      monitorViewports,
      topologyGeneration: displayTopologyGeneration,
    }),
    [
      displayTopologyGeneration,
      monitorViewports,
      overlayViewport,
      webviewDpr,
    ],
  );

  // 18b · 上报 webview viewport 实际 DPR 给 Rust（issue 012 跨屏 drag 修复 v2）。
  //
  // 让 spawn_cursor_feed 用 window.devicePixelRatio 这个固定值算 cursor logical px，
  // 跟 PIXI canvas / spriteScreen 同坐标系。否则跨屏 drag 时 cursor scale 切换 →
  // cursor 数字范围跳跃 → 跟 spriteScreen 矩形错位 → drag 中断 + 完全穿透。
  //
  // DPR 由上面的 WebView display context 统一观测；这里仅把当前快照分发给 Rust。
  useEffect(() => {
    if (!isTauri()) return;
    void invoke("set_pet_webview_dpr", { dpr: displayContext.dpr }).catch(
      (e: unknown) => console.warn("[pet] set_pet_webview_dpr failed:", e),
    );
  }, [displayContext.dpr]);

  // 18 · 启动 push event 订阅 → petStateStore.ingest（与 015 startPetBubbleSubscriber 双独立 listen 对称）
  //
  // **safe-listen pattern**：tauri `listen()` 返 Promise<unlisten>，但 useEffect cleanup
  // 可能早于 promise resolve（StrictMode 双 mount / 快速 hot reload）。如果不处理 cancelled
  // flag，第一次的 listener 就泄漏在 Tauri listeners table 里，第二次再注册重叠 → Tauri 内部
  // 报 `listeners[eventId].handlerId undefined` + dispatch 错乱 → envelope 来了收不到。
  //
  // **safeUnlisten**：hot reload 时 Tauri listeners table 已被新进程清空，旧 unlisten
  // 调用会抛 `listeners[eventId].handlerId undefined`。功能不影响（监听本就断），但
  // console 噪音。统一 try/catch 吞掉。
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    void startPetStateSubscriber().then((u) => {
      if (cancelled) safeUnlisten(u);
      else unlisten = u;
    });
    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, []);

  // 18 · dev-only · AC-3 / AC-4 真跑可观测信号
  // production 静默；dev 时把 phase 切换 + envelope event 类型打到 console，方便手动验证状态机
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    console.info(
      "%c[pet][dev] observability armed",
      "font-weight:bold",
      "· phase 切换 + push envelope 会打到这里。看到这行 = pet 窗 console + 最新代码。",
    );
    let prev: PetPhase = usePetStateStore.getState().phase;
    console.info(`[petState] initial phase = ${prev}`);
    const unsubPhase = usePetStateStore.subscribe((state) => {
      if (state.phase !== prev) {
        console.info(`[petState] phase ${prev} → ${state.phase}`);
        prev = state.phase;
      }
    });
    let cancelled = false;
    let unlistenEnv: (() => void) | null = null;
    if (isTauri()) {
      void listen<PushEnvelope>("agent://push", (e) => {
        const types = e.payload.events.map((ev) => ev.type).join(",") || "(empty)";
        console.info(
          `[push] envelope kind=${e.payload.kind} source=${e.payload.source_kind ?? "(none)"} events=[${types}]`,
        );
      }).then((u) => {
        if (cancelled) safeUnlisten(u);
        else unlistenEnv = u;
      });
    }
    return () => {
      cancelled = true;
      unsubPhase();
      safeUnlisten(unlistenEnv);
    };
  }, []);

  // AC-6 sticky 防抖（17a 原样保留）
  const [stickyVisible, setStickyVisible] = useState(false);
  useEffect(() => {
    if (
      cursorOnSprite ||
      hoverActionBarDom ||
      isDragging ||
      nativeContextMenuOpen
    ) {
      setStickyVisible(true);
    } else {
      const t = setTimeout(() => setStickyVisible(false), 400);
      return () => clearTimeout(t);
    }
  }, [cursorOnSprite, hoverActionBarDom, isDragging, nativeContextMenuOpen]);

  const openChat = () => {
    if (!isTauri()) return;
    void invoke("open_chat");
  };

  const collapseToEdge = () => {
    markPetPositionUserOwned();
    pendingTopologyAnchorRef.current = null;
    setAvatarPlacementIntent(null);
    void setPetAvatarSize("mini");
  };

  const restoreNormal = () => {
    markPetPositionUserOwned();
    pendingTopologyAnchorRef.current = null;
    setAvatarPlacementIntent(null);
    void setPetAvatarSize("normal");
  };

  // 019 · ActionBar 打开设置按钮 → 调 open_settings invoke（参 open_chat 同款 show + focus 逻辑）
  const openSettings = () => {
    void openSettingsWindow();
  };

  const openPetSettings = useCallback(() => {
    void openSettingsWindow({ target: "pet" });
  }, []);

  const getNativeContextMenu = useCallback(() => {
    if (!nativeContextMenuRef.current) {
      nativeContextMenuRef.current = Menu.new({
        items: [
          {
            id: "open-pet-settings",
            text: "桌宠设置",
            action: openPetSettings,
          },
        ],
      }).catch((error) => {
        nativeContextMenuRef.current = null;
        throw error;
      });
    }
    return nativeContextMenuRef.current;
  }, [openPetSettings]);

  useEffect(() => {
    if (isTauri()) {
      void getNativeContextMenu().catch((error) => {
        console.warn("[pet] failed to prepare native context menu:", error);
      });
    }

    return () => {
      const menuPromise = nativeContextMenuRef.current;
      nativeContextMenuRef.current = null;
      if (menuPromise) {
        void menuPromise
          .then((menu) => menu.close())
          .catch((error) =>
            console.warn("[pet] failed to release native context menu:", error),
          );
      }
    };
  }, [getNativeContextMenu]);

  const showNativeContextMenu = useCallback<MouseEventHandler<HTMLDivElement>>(
    (event) => {
      if (!isTauri()) return;

      event.preventDefault();
      event.stopPropagation();
      if (nativeContextMenuPopupInFlightRef.current) return;
      nativeContextMenuPopupInFlightRef.current = true;

      void runNativeContextMenuPopup({
        popup: async () => {
          const menu = await getNativeContextMenu();
          await menu.popup(undefined, getCurrentWindow());
        },
        onOpenChange: setNativeContextMenuOpen,
      })
        .catch((error) => {
          console.warn("[pet] failed to show native context menu:", error);
        })
        .finally(() => {
          nativeContextMenuPopupInFlightRef.current = false;
        });
    },
    [getNativeContextMenu],
  );

  const openFeedback = () => {
    void openFeedbackWindow();
  };

  const openIMConnect = () => {
    if (!isTauri()) return;
    void invoke("open_im_connect");
  };

  const openMemoryInspector = () => {
    if (!isTauri()) return;
    void invoke("open_memory_inspector");
  };

  const openLive2DDebugger = () => {
    if (!isTauri()) return;
    console.info("[pet] open_live2d_debugger requested");
    void invoke("open_live2d_debugger")
      .then(() => console.info("[pet] open_live2d_debugger completed"))
      .catch((error) => console.warn("[pet] open_live2d_debugger failed:", error));
  };

  const startVoiceCall = () => {
    if (isVoiceCallLive(voicePhase)) {
      void focusVoiceCallWindow();
      return;
    }
    void requestStartVoice();
  };

  // 016 M16.9 · dev-only：手动注入测试气泡，跳过 bridge / 真 LLM，方便纯 GUI 验证。
  // release build 时 import.meta.env.DEV=false，整段被 ActionBar 内部 tree-shake 掉。
  const injectShort = () => {
    if (!isTauri()) return;
    void invoke("inject_test_envelope", {
      text: "晚安啦，今天辛苦了，早点休息哦 🐾",
    });
  };
  const injectLong = () => {
    if (!isTauri()) return;
    const long =
      "这是一段超长测试文本，用来验证 bubble window 的尺寸自适应是否生效、长文本是否会被裁切、内部滚动条是否正确工作。" +
      "Lorem ipsum dolor sit amet, consectetur adipiscing elit. ".repeat(8) +
      "结尾标记 ✅";
    void invoke("inject_test_envelope", { text: long });
  };

  return (
    <>
      <div className="contents" onContextMenu={showNativeContextMenu}>
        {selectedEntry?.backend === "live2d" ? (
          <Live2DAvatarSurface
            key={`${selectedEntry.id}@${selectedEntry.contentRevision ?? "builtin"}`}
            entry={selectedEntry}
            initialAnchorPoint={avatarPlacementPoint}
            displayProfile={displayProfile}
            displayContext={displayContext}
            spriteScreen={spriteScreen}
            isDragging={isDragging}
            setSpriteScreen={setSpriteScreenIfChanged}
            setIsDragging={setIsDraggingWithTracking}
            onRuntimeChange={setRuntime}
          />
        ) : selectedEntry?.backend === "spine" && petFacing ? (
          <Spine38AvatarSurface
            key={`${selectedEntry.id}@${selectedEntry.contentRevision ?? "builtin"}`}
            entry={selectedEntry}
            initialAnchorPoint={avatarPlacementPoint}
            displayProfile={displayProfile}
            displayContext={displayContext}
            facing={petFacing}
            setSpriteScreen={setSpriteScreenIfChanged}
            setActionBarAnchor={setActionBarAnchorIfChanged}
            setIsDragging={setIsDraggingWithTracking}
            onFacingChange={handleFacingChange}
            onRuntimeChange={setRuntime}
          />
        ) : null}
      </div>
      {shouldRenderActionBar({
        visible: stickyVisible,
        spriteScreen,
        actionBarAnchor,
        viewport: interactionViewport,
      }) &&
        spriteScreen && (
          <ActionBar
            actionBarAnchor={actionBarAnchor ?? spriteScreen}
            viewport={interactionViewport}
            onMouseEnter={() => setHoverActionBarDom(true)}
            onMouseLeave={() => setHoverActionBarDom(false)}
            onOpenChat={openChat}
            petAvatarSize={petAvatarSize}
            onCollapseToEdge={collapseToEdge}
            onRestoreNormal={restoreNormal}
            onOpenSettings={openSettings}
            onOpenFeedback={openFeedback}
            onOpenIMConnect={openIMConnect}
            onStartVoiceCall={startVoiceCall}
            voiceCallActive={isVoiceCallLive(voicePhase)}
            passthroughDisabled={passthroughDisabled}
            onTogglePassthrough={() => setPassthroughDisabled((v) => !v)}
            onOpenMemoryInspector={openMemoryInspector}
            onOpenLive2DDebugger={openLive2DDebugger}
            onInjectShort={injectShort}
            onInjectLong={injectLong}
          />
        )}
    </>
  );
}
