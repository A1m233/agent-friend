/**
 * 015 R-4.1 · pet 气泡 store + tauri event 订阅器入口。
 *
 * 数据流：Rust push_subscriber 解码 envelope → `emit_to("pet", "agent://push", env)`
 * → 本模块 `listen("agent://push", ...)` → `currentPolicy(env)` 决定是否冒气泡
 * → store 维护当前可见 / 排队条目。
 *
 * Policy 通过 module-level 变量注入而非 store 内闭包绑死——既让 default policy
 * 在 dev 启动期就生效（无需调用方显式注入），又让测试 / 未来产品策略迭代能通过
 * `setPolicy(p)` 替换（AC-3 "policy 可替换"扩展点）。
 *
 * 消失策略（M15.8 真跑后调整）：气泡**常驻**直到用户手动关闭，没有自动消失 timer。
 * 普通主动轮仍保持新内容替换旧内容；Todo Reminder 属于不可互相覆盖的固定事实，按 id
 * 独立保留，由 Sonner 在 bubble window 内堆叠 / 排队。
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

export interface PetBubbleItem extends BubbleItem {
  expanded: boolean;
}

interface PetBubbleState {
  items: PetBubbleItem[];
  /** Reminder 追加；普通主动轮只替换上一条普通主动轮，不覆盖 Reminder。 */
  ingest: (env: PushEnvelope) => void;
  /** 展开指定截断条目看全文（R-4.6.1 不导向 chat 窗）。 */
  expand: (id: string) => void;
  /** 只关闭指定条目；其余 Reminder / 主动轮继续保留。 */
  dismiss: (id: string) => void;
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
  items: [],

  ingest(env) {
    const item = currentPolicy(env);
    if (item === null) return;
    // 同 id 重复丢弃（兜底；envelope.seq 单调递增、Rust 侧已 dedup，这里防御性）
    const currentItems = get().items;
    if (currentItems.some((current) => current.id === item.id)) return;

    const next: PetBubbleItem = { ...item, expanded: false };
    if (item.sourceKind === "todo_reminder") {
      set({ items: [...currentItems, next] });
      return;
    }

    set({
      items: [
        ...currentItems.filter((current) => current.sourceKind === "todo_reminder"),
        next,
      ],
    });
  },

  expand(id) {
    set((state) => ({
      items: state.items.map((item) =>
        item.id === id && !item.expanded ? { ...item, expanded: true } : item,
      ),
    }));
  },

  dismiss(id) {
    set((state) => ({ items: state.items.filter((item) => item.id !== id) }));
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
 * 016 R-4.2.3 · 把 store 是否存在条目同步到 Rust 侧 bubble window 显隐。
 *
 * 监听 `usePetBubbleStore` 的 `items.length`：
 * - 0 → 非 0（首次 ingest）→ invoke `show_bubble`，Rust 侧 `bubble.show()`
 *   并唤醒跟随轮询 task。
 * - 非 0 → 0（最后一条 dismiss）→ invoke `hide_bubble`，Rust 侧
 *   `bubble.hide()` 并标记 `is_visible=false`（task 在下次 tick 进入 park）。
 *
 * 不在 store 内闭包绑死、单独抽 `attachBubbleWindowSync` 函数的原因：
 * - 只在 bubble entry（`bubble/App.tsx`）启动一次订阅；pet entry 同 store 但
 *   不参与显隐控制（items 是 single source of truth，两边读但只 bubble 写控）。
 * - 测试时可不挂订阅（默认 jsdom 环境 `isTauri()` 返 false，invoke 也走 stub）。
 *
 * 返回 unsubscribe；非 Tauri 环境直接返回 no-op。
 */
export function attachBubbleWindowSync(): () => void {
  if (!isTauri()) return () => {};

  const requestWindowVisibility = (visible: boolean) => {
    const command = visible ? "show_bubble" : "hide_bubble";
    void invoke(command).catch((e: unknown) => {
      console.warn(`${command} invoke failed:`, e);
    });
  };

  // 挂载时先让原生窗口向当前 store 状态收敛。只记录初值而不发 IPC 会在 HMR / WebView
  // reload 后留下幽灵窗口：前端 store 重置为空，但 Rust 侧仍保留 reload 前的 visible=true，
  // 后续又没有可见性边沿可触发 hide_bubble。
  let wasVisible = usePetBubbleStore.getState().items.length > 0;
  requestWindowVisibility(wasVisible);

  const unsubscribe = usePetBubbleStore.subscribe((state) => {
    const visible = state.items.length > 0;
    if (visible === wasVisible) return;
    wasVisible = visible;
    requestWindowVisibility(visible);
  });
  return unsubscribe;
}
