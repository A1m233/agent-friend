/**
 * 旧版 push envelope 的气泡兼容 store 与 Tauri 订阅入口。
 *
 * 数据流：Rust push_subscriber 解码 envelope → 向 bubble 页发送 `agent://push`
 * → 本模块 `listen("agent://push", ...)` → `currentPolicy(env)` 决定是否冒气泡
 * → store 保留通过 policy 的条目；呈现层负责排序、退出节点与窗口显隐。
 *
 * Policy 通过 module-level 变量注入而非 store 内闭包绑死——既让 default policy
 * 在 dev 启动期就生效（无需调用方显式注入），又让测试 / 未来产品策略迭代能通过
 * `setPolicy(p)` 替换（AC-3 "policy 可替换"扩展点）。
 *
 * 消失策略（M15.8 真跑后调整）：气泡**常驻**直到用户手动关闭，没有自动消失 timer。
 * 普通主动轮仍保持新内容替换旧内容；Todo Reminder 属于不可互相覆盖的固定事实，按 id
 * 独立保留。统一通知使用 notifications store，本模块不判断其呈现资格。
 *
 * 详见 015 design §5.3。
 */

import { create } from "zustand";
import { listen } from "@tauri-apps/api/event";
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
 * 由 bubble/App.tsx 启动时调用一次：建立 tauri event 订阅，桥接到 store.ingest。
 *
 * 返回 unlisten 函数，供页面 effect 清理订阅。
 * 在非 Tauri 环境（如 vitest / 浏览器调试）下退化为 no-op。
 */
export async function startPetBubbleSubscriber(): Promise<() => void> {
  if (!isTauri()) return () => {};
  const unlisten = await listen<PushEnvelope>("agent://push", (e) => {
    usePetBubbleStore.getState().ingest(e.payload);
  });
  return unlisten;
}
