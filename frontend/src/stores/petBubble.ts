/**
 * 015 R-4.1 · pet 气泡 store + tauri event 订阅器入口。
 *
 * 数据流：Rust push_subscriber 解码 envelope → `emit_to("pet", "agent://push", env)`
 * → 本模块 `listen("agent://push", ...)` → `currentPolicy(env)` 决定是否冒气泡
 * → store 状态机切到 `showing`。
 *
 * Policy 通过 module-level 变量注入而非 store 内闭包绑死——既让 default policy
 * 在 dev 启动期就生效（无需调用方显式注入），又让测试 / 未来产品策略迭代能通过
 * `setPolicy(p)` 替换（AC-3 "policy 可替换"扩展点）。
 *
 * 详见 015 design §5.3。
 */

import { create } from "zustand";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types";
import type { BubbleItem, PushPolicy } from "./petBubblePolicy";
import { defaultPolicy } from "./petBubblePolicy";

/** 气泡显示状态机。 */
export type BubblePhase = "idle" | "showing" | "expanded";

interface PetBubbleState {
  phase: BubblePhase;
  current: BubbleItem | null;
  /** 入口：策略命中则触发气泡显示；未命中则不动。新主动轮替换旧的（最简排队）。 */
  ingest: (env: PushEnvelope) => void;
  /** 用户点击截断态气泡：展开看全文（R-4.6.1 不导向 chat 窗）。 */
  expand: () => void;
  /** 关闭气泡：手动 / 自动 timeout / 被新 item 替换。 */
  dismiss: () => void;
}

/** showing → idle 自动消失时长（ms）；expanded 不自动消失。第一版固定值，未来可移到 user setting。 */
export const AUTO_DISMISS_MS = 10_000;

let currentPolicy: PushPolicy = defaultPolicy;
/** 测试钩子：替换 policy（AC-3 可替换证明）。 */
export function setPolicy(p: PushPolicy): void {
  currentPolicy = p;
}
/** 测试钩子：恢复 defaultPolicy。 */
export function resetPolicy(): void {
  currentPolicy = defaultPolicy;
}

let dismissTimer: ReturnType<typeof setTimeout> | null = null;
function clearTimer(): void {
  if (dismissTimer !== null) {
    clearTimeout(dismissTimer);
    dismissTimer = null;
  }
}

export const usePetBubbleStore = create<PetBubbleState>((set, get) => ({
  phase: "idle",
  current: null,

  ingest(env) {
    const item = currentPolicy(env);
    if (item === null) return;
    // 同 id 重复丢弃（兜底；envelope.seq 单调递增、Rust 侧已 dedup，这里防御性）
    if (get().current?.id === item.id) return;
    clearTimer();
    set({ phase: "showing", current: item });
    dismissTimer = setTimeout(() => {
      // expanded 状态下不自动消失，留给用户手动 dismiss
      if (get().phase === "showing") {
        set({ phase: "idle", current: null });
      }
      dismissTimer = null;
    }, AUTO_DISMISS_MS);
  },

  expand() {
    if (get().phase === "showing") {
      clearTimer();
      set({ phase: "expanded" });
    }
  },

  dismiss() {
    clearTimer();
    set({ phase: "idle", current: null });
  },
}));

/**
 * 由 pet/App.tsx 启动时调用一次：建立 tauri event 订阅，桥接到 store.ingest。
 *
 * 返回 unlisten 函数（pet webview 生命周期不会 unmount，但保留接口对称）。
 * 在非 Tauri 环境（如 vitest / 浏览器调试）下退化为 no-op。
 */
export async function startPetBubbleSubscriber(): Promise<() => void> {
  if (!isTauri()) return () => {};
  const unlisten = await listen<PushEnvelope>("agent://push", (e) => {
    usePetBubbleStore.getState().ingest(e.payload);
  });
  return unlisten;
}
