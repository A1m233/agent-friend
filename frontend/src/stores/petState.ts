/**
 * 18 R-4.2 / R-4.3 · 桌宠状态机 store + push 通道订阅入口。
 *
 * 数据流：Rust push_subscriber 解码 envelope → `emit_to("pet", "agent://push", env)`
 * → 本模块 `listen("agent://push", ...)` → `store.ingest(env)`
 * → `currentPolicy(env, phase)` 决定下一态 → store 切到对应 phase。
 *
 * 与 015 `petBubble` 双件**完全独立**——15 投影到"气泡内容/显隐"，18 投影到"桌宠态"，
 * 两者订阅同一份 envelope（Rust 端 emit 到 bubble 窗 + pet 窗各一份），各管各。
 *
 * Policy 沿 015 同款 module-level 变量注入模式（`setPetStatePolicy` 测试钩子）。
 *
 * 详见 18 design §3.2 / §5.1。
 */

import { create } from "zustand";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";
import {
  defaultPetStatePolicy,
  type PetStatePolicy,
  type PetStateTransition,
} from "./petStatePolicy";

/** 桌宠 4 态最小集（18 R-4.2.1）。 */
export type PetPhase = "idle" | "thinking" | "speaking" | "error";

interface PetStateState {
  phase: PetPhase;
  /** envelope.events 流入：policy 投影 → 切态。 */
  ingest: (env: PushEnvelope) => void;
  /** SSE / 通道错误进 error；外部由 subscriber / Live2D 加载失败处调。 */
  raiseError: () => void;
  /** 用户操作 / 测试钩子：立即回 idle。 */
  reset: () => void;
}

let currentPolicy: PetStatePolicy = defaultPetStatePolicy;
/** 测试钩子：替换 policy。 */
export function setPetStatePolicy(p: PetStatePolicy): void {
  currentPolicy = p;
}
/** 测试钩子：恢复 defaultPetStatePolicy。 */
export function resetPetStatePolicy(): void {
  currentPolicy = defaultPetStatePolicy;
}

export const usePetStateStore = create<PetStateState>((set, get) => ({
  phase: "idle",

  ingest(env) {
    const transition: PetStateTransition = currentPolicy(env, get().phase);
    if (transition.next === get().phase) return;
    if (transition.delayMs && transition.delayMs > 0) {
      setTimeout(() => {
        // 延迟期间被后续 envelope 抢断时 phase 已变，此处不再切（避免误覆盖）
        if (get().phase === transition.from) {
          set({ phase: transition.next });
        }
      }, transition.delayMs);
      return;
    }
    set({ phase: transition.next });

    // 18 · batch envelope 兜底：BedtimeSource / IdleReflectionSource fire_now 是一次性
    // batch（整个 envelope 同时含几百 text_delta + 最后一个 done）。policy 优先级 1 是
    // text_delta → 决定 speaking，**不返 done transition**。speaking 之后没第二个 envelope
    // 触发 done→idle，卡死 speaking。这里在 store 层兜底：进入 speaking 且 envelope 同帧
    // 含 done → schedule 一次延迟 idle 切回，时长 = lip-sync 估算（按总文本字数 × 80ms）
    // + 300ms 缓冲。仍保留延迟期被新 envelope 抢断的判定（phase === "speaking" 才切）。
    if (
      env.kind === "agent_turn" &&
      transition.next === "speaking" &&
      env.events.some((e) => e.type === "done")
    ) {
      const totalChars = env.events
        .filter((e) => e.type === "text_delta")
        .reduce(
          (sum, e) => sum + (typeof e.text === "string" ? e.text.length : 0),
          0,
        );
      const lipSyncMs = Math.max(800, totalChars * 80) + 300;
      setTimeout(() => {
        if (get().phase === "speaking") {
          set({ phase: "idle" });
        }
      }, lipSyncMs);
    }
  },

  raiseError() {
    set({ phase: "error" });
  },

  reset() {
    set({ phase: "idle" });
  },
}));

/**
 * 由 pet/App.tsx 启动时调用一次：建立 tauri event 订阅，桥接到 store.ingest。
 *
 * pet webview 生命周期不会 unmount，但保留 unlisten 接口对称（与 015 `startPetBubbleSubscriber`
 * 同款形态）。非 Tauri 环境（vitest / 浏览器调试）退化为 no-op。
 */
export async function startPetStateSubscriber(): Promise<() => void> {
  if (!isTauri()) return () => {};
  const unlisten = await listen<PushEnvelope>("agent://push", (e) => {
    usePetStateStore.getState().ingest(e.payload);
  });
  return unlisten;
}
