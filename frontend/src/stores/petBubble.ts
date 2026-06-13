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
 * 消失策略（M15.8 真跑后调整）：气泡**常驻**直到 (a) 用户点关闭按钮 / (b) 新主动轮
 * envelope 替换。**没有自动消失 timer**——auto-dismiss 在真跑里发现容易错过主动轮，
 * 跟"在场感"产品诉求冲突。design §6.3 留过"未来可移到 user setting"的口子，本期
 * 直接走"常驻 + 手动关闭"。
 *
 * 详见 015 design §5.3。
 */

import { create } from "zustand";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";
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
  /** 关闭气泡：手动 / 被新 item 替换。**唯一让气泡消失的入口**（除了被新 envelope 替换）。 */
  dismiss: () => void;
}

let currentPolicy: PushPolicy = defaultPolicy;
/** 测试钩子：替换 policy（AC-3 可替换证明）。 */
export function setPolicy(p: PushPolicy): void {
  currentPolicy = p;
}
/** 测试钩子：恢复 defaultPolicy。 */
export function resetPolicy(): void {
  currentPolicy = defaultPolicy;
}

export const usePetBubbleStore = create<PetBubbleState>((set, get) => ({
  phase: "idle",
  current: null,

  ingest(env) {
    const item = currentPolicy(env);
    if (item === null) return;
    // 同 id 重复丢弃（兜底；envelope.seq 单调递增、Rust 侧已 dedup，这里防御性）
    if (get().current?.id === item.id) return;
    set({ phase: "showing", current: item });
  },

  expand() {
    if (get().phase === "showing") {
      set({ phase: "expanded" });
    }
  },

  dismiss() {
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

/**
 * 016 R-4.2.3 · 把 store phase 状态同步到 Rust 侧 bubble window 显隐。
 *
 * 监听 `usePetBubbleStore` 的 `phase`：
 * - idle → 非 idle（首次 ingest）→ invoke `show_bubble`，Rust 侧 `bubble.show()`
 *   并唤醒跟随轮询 task。
 * - 非 idle → idle（dismiss / 替换为 null）→ invoke `hide_bubble`，Rust 侧
 *   `bubble.hide()` 并标记 `is_visible=false`（task 在下次 tick 进入 park）。
 *
 * 不在 store 内闭包绑死、单独抽 `attachBubbleWindowSync` 函数的原因：
 * - 只在 bubble entry（`bubble/App.tsx`）启动一次订阅；pet entry 同 store 但
 *   不参与显隐控制（store 的 phase 是 single source of truth，两边读但只 bubble 写控）。
 * - 测试时可不挂订阅（默认 jsdom 环境 `isTauri()` 返 false，invoke 也走 stub）。
 *
 * 返回 unsubscribe；非 Tauri 环境直接返回 no-op。
 */
export function attachBubbleWindowSync(): () => void {
  if (!isTauri()) return () => {};
  // 初值用 store 当前 phase，避免错过启动那一刻已经非 idle 的边界情况
  // （实际上 bubble entry 启动时 store 必定是 idle 初始态，但守一下不亏）
  let lastPhase: BubblePhase = usePetBubbleStore.getState().phase;
  const unsubscribe = usePetBubbleStore.subscribe((state) => {
    const next = state.phase;
    if (next === lastPhase) return;
    const becameVisible = lastPhase === "idle" && next !== "idle";
    const becameHidden = lastPhase !== "idle" && next === "idle";
    lastPhase = next;
    if (becameVisible) {
      void invoke("show_bubble").catch((e: unknown) => {
        console.warn("show_bubble invoke failed:", e);
      });
    } else if (becameHidden) {
      void invoke("hide_bubble").catch((e: unknown) => {
        console.warn("hide_bubble invoke failed:", e);
      });
    }
  });
  return unsubscribe;
}
