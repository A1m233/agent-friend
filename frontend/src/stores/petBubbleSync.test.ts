/** 016 + 070 · store 条目可见性 → 独立 bubble window 显隐 IPC。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { invokeMock, isTauriMock } = vi.hoisted(() => ({
  invokeMock: vi.fn<(cmd: string, args?: unknown) => Promise<unknown>>(() => Promise.resolve()),
  isTauriMock: vi.fn<() => boolean>(() => true),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
vi.mock("@/utils/tauri", () => ({ isTauri: isTauriMock }));

import {
  attachBubbleWindowSync,
  usePetBubbleStore,
  type PetBubbleItem,
} from "./petBubble";

const itemA: PetBubbleItem = {
  id: "a",
  text: "晚安啦",
  sourceKind: "cron:bedtime",
  expanded: false,
};
const itemB: PetBubbleItem = {
  id: "b",
  text: "再说一遍",
  sourceKind: "todo_reminder",
  expanded: false,
};

beforeEach(() => {
  invokeMock.mockClear();
  isTauriMock.mockReset();
  isTauriMock.mockReturnValue(true);
  usePetBubbleStore.setState({ items: [] });
});

let unsubscribe: () => void = () => {};
afterEach(() => {
  unsubscribe();
  unsubscribe = () => {};
});

describe("attachBubbleWindowSync", () => {
  it("挂载时条目为空会主动隐藏可能残留的原生窗口", () => {
    unsubscribe = attachBubbleWindowSync();
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("hide_bubble");
  });

  it("挂载时已有条目会主动显示原生窗口", () => {
    usePetBubbleStore.setState({ items: [itemA] });
    unsubscribe = attachBubbleWindowSync();
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("show_bubble");
  });

  it("空 → 非空只触发一次 show_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    invokeMock.mockClear();
    usePetBubbleStore.setState({ items: [itemA] });
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("show_bubble");
  });

  it("非空列表追加或替换条目不重复触发 show_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ items: [itemA] });
    invokeMock.mockClear();
    usePetBubbleStore.setState({ items: [itemA, itemB] });
    usePetBubbleStore.setState({ items: [itemB] });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("移除最后一个条目触发一次 hide_bubble", () => {
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ items: [itemA] });
    invokeMock.mockClear();
    usePetBubbleStore.setState({ items: [] });
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("hide_bubble");
  });

  it("非 Tauri 环境返回 no-op，条目变化不触发 invoke", () => {
    isTauriMock.mockReturnValue(false);
    unsubscribe = attachBubbleWindowSync();
    usePetBubbleStore.setState({ items: [itemA] });
    usePetBubbleStore.setState({ items: [] });
    expect(invokeMock).not.toHaveBeenCalled();
  });
});
