/**
 * 016 M16.7 · `attachBubbleWindowSync` 单测。
 *
 * 覆盖 R-4.2.3 store phase 变化 → invoke `show_bubble` / `hide_bubble` 的同步语义。
 * 也是 AC-2 "气泡走独立 window" 在前端侧的硬指标 —— 验证 store 状态确实驱动了
 * Rust 侧 bubble window 的显隐 IPC（具体 window 是否真冒在 M16.9 端到端 dev 真跑验证）。
 *
 * 测试策略：
 * - mock `@tauri-apps/api/core` 的 `invoke` —— 拦截后断言调用次数 + 参数
 * - mock `@/utils/tauri` 的 `isTauri` —— 控制 Tauri / 非 Tauri 分支
 * - 直接 `usePetBubbleStore.setState(...)` 模拟 phase 变化（不依赖 PushPolicy / 真实 envelope）
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock 工厂会被提升到文件顶部，必须用 vi.hoisted 让 mock 函数也跟着提升
// 否则 invokeMock / isTauriMock 在 mock 工厂里访问时还没初始化。
const { invokeMock, isTauriMock } = vi.hoisted(() => ({
  invokeMock: vi.fn<(cmd: string, args?: unknown) => Promise<unknown>>(() => Promise.resolve()),
  isTauriMock: vi.fn<() => boolean>(() => true),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));
vi.mock("@/utils/tauri", () => ({
  isTauri: isTauriMock,
}));

import { attachBubbleWindowSync, usePetBubbleStore } from "./petBubble";
import type { BubbleItem } from "./petBubblePolicy";

const itemA: BubbleItem = { id: "a", text: "晚安啦", sourceKind: "cron:bedtime" };
const itemB: BubbleItem = { id: "b", text: "再说一遍", sourceKind: "cron:bedtime" };

beforeEach(() => {
  invokeMock.mockClear();
  isTauriMock.mockReset();
  isTauriMock.mockReturnValue(true);
  usePetBubbleStore.setState({ phase: "idle", current: null });
});

let unsubscribe: () => void = () => {};
afterEach(() => {
  unsubscribe();
  unsubscribe = () => {};
});

describe("attachBubbleWindowSync", () => {
  it("idle → showing 触发一次 show_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ phase: "showing", current: itemA });
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("show_bubble");
  });

  it("showing → showing（新 envelope 替换 current）不重复触发 show_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ phase: "showing", current: itemA });
    invokeMock.mockClear();
    // 模拟新主动轮替换 current；phase 不变，sync 不应再发 IPC
    usePetBubbleStore.setState({ phase: "showing", current: itemB });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("showing → expanded 仍非 idle，不触发 hide_bubble / show_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ phase: "showing", current: itemA });
    invokeMock.mockClear();
    usePetBubbleStore.setState({ phase: "expanded" });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("expanded → idle（dismiss）触发一次 hide_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ phase: "showing", current: itemA });
    usePetBubbleStore.setState({ phase: "expanded" });
    invokeMock.mockClear();
    usePetBubbleStore.setState({ phase: "idle", current: null });
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("hide_bubble");
  });

  it("idle → idle（dismiss 后再 dismiss）不重复触发 hide_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ phase: "showing", current: itemA });
    usePetBubbleStore.setState({ phase: "idle", current: null });
    invokeMock.mockClear();
    usePetBubbleStore.setState({ phase: "idle", current: null });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("非 Tauri 环境 attachBubbleWindowSync 返回 no-op；phase 变化不触发 invoke", () => {
    isTauriMock.mockReturnValue(false);
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ phase: "showing", current: itemA });
    usePetBubbleStore.setState({ phase: "idle", current: null });
    expect(invokeMock).not.toHaveBeenCalled();
  });
});
