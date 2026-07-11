import { useEffect, useMemo, useRef } from "react";
import { listen } from "@tauri-apps/api/event";
import { Priority } from "easy-live2d";
import type { Live2DSprite } from "easy-live2d";
import type { Live2DAvatarModelEntry } from "@/avatar-models";
import type { PushEnvelope } from "@/types/push";
import { isTauri } from "@/utils/tauri";
import { usePixiAvatarSlot } from "@/pages/pet/usePixiAvatarSlot";
import {
  usePetInteractions,
  type PetInteractionCallbacks,
} from "@/pages/pet/usePetInteractions";
import { useLive2DDebugBridge } from "@/pages/pet/useLive2DDebugBridge";
import { usePetLive2D } from "@/pet/usePetLive2D";
import { TextCadenceMouthDriver } from "@/pet/MouthDriver";
import { alphaHitTestFromApp } from "@/pet/avatarRuntime/framebuffer";
import type { PetDisplayProfile } from "@/pages/pet/petDisplayProfile";
import type { PetWebviewDisplayContext } from "@/pages/pet/petWebviewDisplayContext";
import type {
  AvatarActionResult,
  PetAvatarRuntime,
  PetAvatarRuntimePhase,
} from "@/pet/avatarRuntime/types";
import {
  usePetStateStore,
  type PetPhase,
} from "@/stores/petState";

interface Props {
  entry: Live2DAvatarModelEntry;
  initialAnchorPoint: { x: number; y: number } | null;
  displayProfile: PetDisplayProfile;
  displayContext: PetWebviewDisplayContext;
  spriteScreen: { x: number; y: number; w: number; h: number } | null;
  isDragging: boolean;
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void;
  setIsDragging: (v: boolean) => void;
  onRuntimeChange: (runtime: PetAvatarRuntime | null) => void;
}

function safeUnlisten(fn: (() => void) | null | undefined): void {
  if (!fn) return;
  try {
    fn();
  } catch {
    /* stale-cleanup race ignored */
  }
}

function isPetPhase(phase: PetAvatarRuntimePhase): phase is PetPhase {
  return phase === "idle" || phase === "thinking" || phase === "speaking" || phase === "error";
}

function startLive2DMotion(
  sprite: Live2DSprite | null,
  entry: Live2DAvatarModelEntry,
  phase: PetAvatarRuntimePhase,
): AvatarActionResult {
  if (!sprite) return { status: "skipped", reason: "live2d sprite not ready" };
  if (!isPetPhase(phase)) return { status: "skipped", reason: `unsupported Live2D phase ${phase}` };

  const group = entry.live2d.motionGroups[phase];
  if (!group) return { status: "skipped", reason: `no Live2D motion group for ${phase}` };

  sprite
    .startMotion({
      group,
      no: entry.live2d.motionNo[phase] ?? 0,
      priority: phase === "idle" ? Priority.Idle : Priority.Normal,
    })
    .catch((err) => console.warn("[Live2DAvatarSurface] motion failed:", err));
  return { status: "played", animation: group };
}

export function Live2DAvatarSurface({
  entry,
  initialAnchorPoint,
  displayProfile,
  displayContext,
  spriteScreen,
  isDragging,
  setSpriteScreen,
  setIsDragging,
  onRuntimeChange,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const interactionsRef = useRef<PetInteractionCallbacks | null>(null);

  const { slotRef, app, invalidateAnchor, alphaScanGivenUpRef } = usePixiAvatarSlot(
    stageRef,
    setSpriteScreen,
    setIsDragging,
    { interactionsRef, initialAnchorPoint, displayProfile, displayContext },
  );
  const { spriteRef, spriteReady } = usePetLive2D(slotRef, app, entry, invalidateAnchor);
  const driver = useMemo(() => new TextCadenceMouthDriver(), []);

  useEffect(() => {
    let prevPhase: PetPhase = usePetStateStore.getState().phase;
    const unsub = usePetStateStore.subscribe((state) => {
      const nextPhase = state.phase;
      if (nextPhase === prevPhase) return;
      const sprite = spriteRef.current;
      if (prevPhase !== "speaking" && nextPhase === "speaking" && sprite) {
        driver.attach(sprite);
      } else if (prevPhase === "speaking" && nextPhase !== "speaking") {
        driver.detach();
      }
      prevPhase = nextPhase;
    });
    return () => {
      unsub();
      driver.detach();
    };
  }, [driver, spriteRef]);

  const { onSlotClick, onSlotDragMove, debugControls } = usePetInteractions(
    spriteRef,
    spriteReady,
    spriteScreen,
    isDragging,
    driver,
  );

  useLive2DDebugBridge({
    spriteRef,
    spriteReady,
    debugControls,
  });

  useEffect(() => {
    interactionsRef.current = { onSlotClick, onSlotDragMove };
  }, [onSlotClick, onSlotDragMove]);

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    void listen<PushEnvelope>("agent://push", (e) => {
      if (usePetStateStore.getState().phase !== "speaking") return;
      for (const ev of e.payload.events) {
        if (ev.type === "text_delta" && typeof ev.text === "string") {
          driver.onTextDelta?.(ev.text);
        }
      }
    }).then((u) => {
      if (cancelled) safeUnlisten(u);
      else unlisten = u;
    });
    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, [driver]);

  const runtime = useMemo<PetAvatarRuntime>(
    () => ({
      kind: "live2d",
      modelId: entry.id,
      capabilities: entry.capabilities,
      ready: spriteReady,
      setPhase: (phase) => startLive2DMotion(spriteRef.current, entry, phase),
      playAction: (action) => {
        if (action !== "tap") {
          return { status: "skipped", reason: `unsupported Live2D action ${action}` };
        }
        const group = entry.live2d.motionGroups.tap;
        if (!group || !spriteRef.current) {
          return { status: "skipped", reason: "Live2D tap motion unavailable" };
        }
        spriteRef.current
          .startMotion({
            group,
            no: entry.live2d.motionNo.tap ?? 0,
            priority: Priority.Normal,
          })
          .catch((err) => console.warn("[Live2DAvatarSurface] tap motion failed:", err));
        debugControls.triggerTapParamsOnly();
        return { status: "played", animation: group };
      },
      alphaHitTest: (cursor) => {
        if (alphaScanGivenUpRef.current) return true;
        return app ? alphaHitTestFromApp(app, cursor) : false;
      },
    }),
    [alphaScanGivenUpRef, app, debugControls, entry, spriteReady, spriteRef],
  );

  useEffect(() => {
    onRuntimeChange(runtime);
    return () => onRuntimeChange(null);
  }, [onRuntimeChange, runtime]);

  return <div ref={stageRef} className="fixed inset-0 overflow-hidden bg-transparent" />;
}
