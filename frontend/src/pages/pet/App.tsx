import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";

import { ActionBar } from "./ActionBar";
import { DevPassthroughToggle } from "./DevPassthroughToggle";
import { IMConnectDialog } from "@/components/im/IMConnectDialog";
import { usePetPassthrough } from "./usePetPassthrough";
import {
  getPetViewport,
  resolvePetDisplayProfile,
  type PetDisplayMode,
  type PetViewport,
} from "./petDisplayProfile";
import {
  resolveInstalledAvatarModelSelection,
  useInstalledAvatarModelCatalog,
} from "@/avatar-models";
import { useSetting } from "@/lib/settings";
import { Live2DAvatarSurface } from "@/pet/avatarRuntime/live2d/Live2DAvatarSurface";
import { Spine38AvatarSurface } from "@/pet/avatarRuntime/spine38/Spine38AvatarSurface";
import type { PetAvatarRuntime } from "@/pet/avatarRuntime/types";
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

interface PetRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface PetPoint {
  x: number;
  y: number;
}

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
  const [spriteScreen, setSpriteScreen] = useState<PetRect | null>(null);
  const [actionBarAnchor, setActionBarAnchor] = useState<PetRect | null>(null);
  const [avatarPlacementPoint, setAvatarPlacementPoint] = useState<PetPoint | null>(null);
  // AC-6 hover gate · 双源：cursorOnSprite（Rust 60Hz cursor channel 驱动）
  //                + hoverActionBarDom（ActionBar 自身 onMouseEnter/Leave 驱动）
  // 单一 hoverActionBar 不够：cursor 离开 sprite 后 setIgnoreCursorEvents 切 true，
  // PIXI 不再收事件、pointerout 不触发；改由 usePetPassthrough 拿 Rust cursor 数据每帧判断。
  const [cursorOnSprite, setCursorOnSprite] = useState(false);
  const [hoverActionBarDom, setHoverActionBarDom] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  // 18 · dev-only 切穿透：开 inspector 调试时点"穿透 OFF"让 pet 变实窗，inspector 可点
  const [passthroughDisabled, setPassthroughDisabled] = useState(false);
  // 022 · IM 接入面板 dialog 开关(同窗 dialog,不另开 webview)
  const [imDialogOpen, setImDialogOpen] = useState(false);
  const voicePhase = useVoiceStore((s) => s.phase);
  const requestStartVoice = useVoiceStore((s) => s.requestStartFromAnyWindow);
  const [runtime, setRuntime] = useState<PetAvatarRuntime | null>(null);
  const [avatarModelId] = useSetting("avatarModelId");
  const [petDisplayMode, setPetDisplayMode] = useSetting("petDisplayMode");
  const previousPetDisplayModeRef = useRef<PetDisplayMode | null>(null);
  const initialPlacementPendingRef = useRef(true);
  const [viewport, setViewport] = useState<PetViewport>(() => getPetViewport());
  const {
    entries: installedAvatarModels,
    error: installedAvatarModelError,
  } = useInstalledAvatarModelCatalog();
  const selectedEntry = resolveInstalledAvatarModelSelection(
    installedAvatarModels,
    avatarModelId,
  );

  useEffect(() => {
    const updateViewport = () => setViewport(getPetViewport());
    updateViewport();
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

  const displayProfile = useMemo(
    () => {
      const anchor = actionBarAnchor ?? spriteScreen;
      return resolvePetDisplayProfile(
        petDisplayMode,
        anchor,
        viewport,
        {
          previousMode:
            initialPlacementPendingRef.current && anchor
              ? null
              : previousPetDisplayModeRef.current,
        },
      );
    },
    [actionBarAnchor, petDisplayMode, spriteScreen, viewport],
  );

  useEffect(() => {
    previousPetDisplayModeRef.current = petDisplayMode;
  }, [petDisplayMode]);

  useEffect(() => {
    if (actionBarAnchor ?? spriteScreen) {
      initialPlacementPendingRef.current = false;
    }
  }, [actionBarAnchor, spriteScreen]);

  useEffect(() => {
    setRuntime(null);
    setSpriteScreen(null);
    setActionBarAnchor(null);
    publishAnchorDiagnostics({
      modelId: selectedEntry?.id ?? null,
      backend: selectedEntry?.backend ?? null,
      spriteScreen: null,
      visibleBounds: null,
      actionBarAnchor: null,
      actionBarComputed: null,
      actionBarRect: null,
    });
  }, [selectedEntry?.backend, selectedEntry?.id]);

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
    });
  }, [actionBarAnchor, selectedEntry?.backend, selectedEntry?.id, spriteScreen]);

  useEffect(() => {
    const placementRect = actionBarAnchor ?? spriteScreen;
    if (!placementRect) return;
    setAvatarPlacementPoint({
      x: placementRect.x + placementRect.w / 2,
      y: placementRect.y + placementRect.h / 2,
    });
  }, [actionBarAnchor, spriteScreen]);

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

  // 18b · 上报 webview viewport 实际 DPR 给 Rust（issue 012 跨屏 drag 修复 v2）。
  //
  // 让 spawn_cursor_feed 用 window.devicePixelRatio 这个固定值算 cursor logical px，
  // 跟 PIXI canvas / spriteScreen 同坐标系。否则跨屏 drag 时 cursor scale 切换 →
  // cursor 数字范围跳跃 → 跟 spriteScreen 矩形错位 → drag 中断 + 完全穿透。
  //
  // matchMedia 监 DPR 变化（罕见，但 mac 跨外接 Retina / Win 改 display scale 偶发）：
  // 写入当前 dpr 的 `(resolution: Xdppx)` query，DPR 变化时 query 不再 match → change。
  useEffect(() => {
    if (!isTauri()) return;
    const report = () => {
      void invoke("set_pet_webview_dpr", { dpr: window.devicePixelRatio }).catch(
        (e: unknown) => console.warn("[pet] set_pet_webview_dpr failed:", e),
      );
    };
    report();
    const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    const onChange = () => report();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

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
    if (cursorOnSprite || hoverActionBarDom || isDragging) {
      setStickyVisible(true);
    } else {
      const t = setTimeout(() => setStickyVisible(false), 400);
      return () => clearTimeout(t);
    }
  }, [cursorOnSprite, hoverActionBarDom, isDragging]);

  const openChat = () => {
    if (!isTauri()) return;
    void invoke("open_chat");
  };

  const collapseToEdge = () => {
    void setPetDisplayMode("edgeRecall");
  };

  const restoreNormal = () => {
    void setPetDisplayMode("normal");
  };

  // 019 · ActionBar 打开设置按钮 → 调 open_settings invoke（参 open_chat 同款 show + focus 逻辑）
  const openSettings = () => {
    if (!isTauri()) return;
    void invoke("open_settings");
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
      {selectedEntry?.backend === "live2d" ? (
        <Live2DAvatarSurface
          key={selectedEntry.id}
          entry={selectedEntry}
          initialAnchorPoint={avatarPlacementPoint}
          displayProfile={displayProfile}
          spriteScreen={spriteScreen}
          isDragging={isDragging}
          setSpriteScreen={setSpriteScreen}
          setIsDragging={setIsDragging}
          onRuntimeChange={setRuntime}
        />
      ) : selectedEntry?.backend === "spine" ? (
        <Spine38AvatarSurface
          key={selectedEntry.id}
          entry={selectedEntry}
          initialAnchorPoint={avatarPlacementPoint}
          displayProfile={displayProfile}
          setSpriteScreen={setSpriteScreen}
          setActionBarAnchor={setActionBarAnchor}
          setIsDragging={setIsDragging}
          onRuntimeChange={setRuntime}
        />
      ) : null}
      <DevPassthroughToggle
        disabled={passthroughDisabled}
        onToggle={() => setPassthroughDisabled((v) => !v)}
      />
      {spriteScreen && (
        <ActionBar
          actionBarAnchor={actionBarAnchor ?? spriteScreen}
          visible={stickyVisible}
          onMouseEnter={() => setHoverActionBarDom(true)}
          onMouseLeave={() => setHoverActionBarDom(false)}
          onOpenChat={openChat}
          petDisplayMode={petDisplayMode}
          onCollapseToEdge={collapseToEdge}
          onRestoreNormal={restoreNormal}
          onOpenSettings={openSettings}
          onOpenIMConnect={() => setImDialogOpen(true)}
          onStartVoiceCall={startVoiceCall}
          voiceCallActive={isVoiceCallLive(voicePhase)}
          onOpenMemoryInspector={openMemoryInspector}
          onOpenLive2DDebugger={openLive2DDebugger}
          onInjectShort={injectShort}
          onInjectLong={injectLong}
        />
      )}
      <IMConnectDialog open={imDialogOpen} onOpenChange={setImDialogOpen} />
    </>
  );
}
