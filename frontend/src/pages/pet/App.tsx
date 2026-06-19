import { useEffect, useMemo, useRef, useState } from "react";
import * as PIXI from "pixi.js";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Priority } from "easy-live2d";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";

import { ActionBar } from "./ActionBar";
import { DevPassthroughToggle } from "./DevPassthroughToggle";
import { IMConnectDialog } from "@/components/im/IMConnectDialog";
import { usePixiAvatarSlot } from "./usePixiAvatarSlot";
import { usePetPassthrough } from "./usePetPassthrough";
import { usePetInteractions } from "./usePetInteractions";
import { usePetLive2D } from "@/pet/usePetLive2D";
import { PET_LIVE2D_CONFIG } from "@/pet/live2dConfig";
import { TextCadenceMouthDriver } from "@/pet/MouthDriver";
import {
  startPetStateSubscriber,
  usePetStateStore,
  type PetPhase,
} from "@/stores/petState";

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
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [spriteScreen, setSpriteScreen] = useState<{
    x: number;
    y: number;
    w: number;
    h: number;
  } | null>(null);
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

  // 17a · PIXI lifecycle + avatar slot Container + drag + sprite world position 上报
  // anchor / hitArea / spriteScreen 自动从 alpha-scan 推（一次锁定 visible bounds，不抖）；
  // invalidateAnchor 由 usePetLive2D 在 Live2DSprite ready 后调用，让 anchor 重新扫到真 Hiyori bounds
  // 024 · interactionsRef 延迟绑定：usePixiAvatarSlot 比 usePetLive2D/usePetInteractions 早调用，
  // 但 callbacks 又依赖 spriteRef/driver。用 ref 在 hooks 顺序合法的前提下做延迟绑定。
  const interactionsRef = useRef<{ onSlotClick: (e: PIXI.FederatedPointerEvent) => void; onSlotDragMove: (vx: number, vy: number) => void } | null>(null);

  const { slotRef, app, invalidateAnchor, alphaScanGivenUpRef } = usePixiAvatarSlot(
    stageRef,
    setSpriteScreen,
    setIsDragging,
    { interactionsRef },
  );

  // 18 · 加载 Hiyori Live2DSprite 到 avatar-slot 内（slot 外层 plumbing 不动）
  const { spriteRef, spriteReady } = usePetLive2D(slotRef, app, invalidateAnchor);

  // 18 · lip-sync MouthDriver 与状态机联动
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

  // 024 · 鼠标输入反馈（gaze / tap / drag）集中调度
  const { onSlotClick, onSlotDragMove } = usePetInteractions(
    spriteRef,
    spriteReady,
    spriteScreen,
    isDragging,
    driver,
  );

  // 把 callbacks 回填给 slot handler
  useEffect(() => {
    interactionsRef.current = { onSlotClick, onSlotDragMove };
  }, [onSlotClick, onSlotDragMove]);

  // 17a · cursor passthrough（DOM data-hit 优先 + 18 alpha hit-test 主路径 + spriteScreen 矩形兜底 + isDragging 互锁）
  // + AC-6 cursorOnSprite 驱动 + 18 dev-only disabled 切换
  // + 18b alphaScanGivenUpRef：Win mixed DPR 下 readPixels 失效时 alpha hit-test 跳过（issue 012）
  usePetPassthrough({
    isDragging,
    spriteScreen,
    app,
    alphaScanGivenUpRef,
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

  // 18 · speaking 态下 text_delta 副流转发给 driver（独立 listen 副流，不污染 petState ingest）
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

  // 18 · 状态机 → Live2DSprite motion 派发（thinking / speaking / error 切动作）
  useEffect(() => {
    return usePetStateStore.subscribe((state) => {
      const sprite = spriteRef.current;
      if (!sprite) return;
      const group = PET_LIVE2D_CONFIG.motionGroups[state.phase];
      if (!group) return; // null → 不切动作，让默认 idle 继续跑
      sprite
        .startMotion({ group, no: 0, priority: Priority.Normal })
        .catch((err) => console.warn("[PetApp] motion failed:", err));
    });
  }, [spriteRef]);

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

  // 019 · ActionBar 隐藏桌宠按钮 → 调 hide_pet invoke（pet 窗整体 hide）。
  // 唤回路径：仅系统托盘菜单 "显示/隐藏桌宠"（toggle_pet）；从桌面直接唤回是
  // [issue 013](../../../../docs/issues/013-pet-recall-from-desktop/) 跟踪的独立缺口。
  const hidePet = () => {
    if (!isTauri()) return;
    void invoke("hide_pet");
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
      <div
        ref={stageRef}
        className="fixed inset-0 overflow-hidden bg-transparent"
      />
      <DevPassthroughToggle
        disabled={passthroughDisabled}
        onToggle={() => setPassthroughDisabled((v) => !v)}
      />
      {spriteScreen && (
        <ActionBar
          spriteScreen={spriteScreen}
          visible={stickyVisible}
          onMouseEnter={() => setHoverActionBarDom(true)}
          onMouseLeave={() => setHoverActionBarDom(false)}
          onOpenChat={openChat}
          onHidePet={hidePet}
          onOpenSettings={openSettings}
          onOpenIMConnect={() => setImDialogOpen(true)}
          onOpenMemoryInspector={openMemoryInspector}
          onInjectShort={injectShort}
          onInjectLong={injectLong}
        />
      )}
      <IMConnectDialog open={imDialogOpen} onOpenChange={setImDialogOpen} />
    </>
  );
}
