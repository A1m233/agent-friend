import { useEffect, type RefObject } from "react";
import { emitTo, listen } from "@tauri-apps/api/event";
import type { Live2DSprite } from "easy-live2d";
import { isTauri } from "@/utils/tauri";
import { usePetStateStore } from "@/stores/petState";
import {
  executeLive2DDebugCommand,
  type Live2DDebugExecutorEnv,
} from "@/pet/live2dDebugger/executor";
import { loadCurrentMotionCatalog } from "@/pet/live2dDebugger/catalog";
import {
  LIVE2D_DEBUGGER_COMMAND_EVENT,
  LIVE2D_DEBUGGER_RESPONSE_EVENT,
  LIVE2D_DEBUGGER_WINDOW_LABEL,
  type Live2DDebugCommand,
} from "@/pet/live2dDebugger/protocol";

interface Live2DDebugControls {
  triggerTapFeedback: () => "played" | "skipped_by_phase";
  triggerTapParamsOnly: () => "played" | "skipped_by_phase";
}

interface UseLive2DDebugBridgeParams {
  spriteRef: RefObject<Live2DSprite | null>;
  spriteReady: boolean;
  debugControls: Live2DDebugControls;
}

function safeUnlisten(fn: (() => void) | null | undefined): void {
  if (!fn) return;
  try {
    fn();
  } catch {
    /* stale-cleanup race ignored */
  }
}

export function useLive2DDebugBridge({
  spriteRef,
  spriteReady,
  debugControls,
}: UseLive2DDebugBridgeParams): void {
  useEffect(() => {
    if (!import.meta.env.DEV || !isTauri()) return;

    let cancelled = false;
    let unlisten: (() => void) | null = null;

    const env: Live2DDebugExecutorEnv = {
      getSprite: () => spriteRef.current,
      getSpriteReady: () => spriteReady,
      getCatalog: () => loadCurrentMotionCatalog(),
      getPhase: () => usePetStateStore.getState().phase,
      actions: debugControls,
      random: () => Math.random(),
    };

    void listen<Live2DDebugCommand>(LIVE2D_DEBUGGER_COMMAND_EVENT, async (event) => {
      console.info("[live2d-debugger] command received:", event.payload.kind);
      const response = await executeLive2DDebugCommand(event.payload, env);
      await emitTo(
        LIVE2D_DEBUGGER_WINDOW_LABEL,
        LIVE2D_DEBUGGER_RESPONSE_EVENT,
        response,
      ).catch((error: unknown) => {
        console.warn("[live2d-debugger] emit response failed:", error);
      });
      console.info(
        "[live2d-debugger] response emitted:",
        response.ok ? "ok" : "error",
        response.requestId,
      );
    }).then((u) => {
      if (cancelled) safeUnlisten(u);
      else {
        unlisten = u;
        console.info("[live2d-debugger] pet bridge ready");
      }
    }).catch((error: unknown) => {
      if (!cancelled) {
        console.warn("[live2d-debugger] pet bridge listen failed:", error);
      }
    });

    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, [debugControls, spriteReady, spriteRef]);
}
