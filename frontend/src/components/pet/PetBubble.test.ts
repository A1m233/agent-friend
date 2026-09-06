// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentNotification } from "@/types/notification";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(), listen: vi.fn(), presentation: vi.fn(),
  snapshot: vi.fn(), prepare: vi.fn(), presented: vi.fn(), dismiss: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));
vi.mock("@/services/api/notifications", () => ({ notificationsApi: mocks }));
vi.mock("./useBubblePresentation", () => ({ useBubblePresentation: mocks.presentation }));

import { recordNotificationPresented, resetNotificationsForTests, useNotificationStore } from "@/stores/notifications";
import { usePetBubbleStore } from "@/stores/petBubble";
import { PetBubble } from "./PetBubble";

const LONG_TEXT = "这是一条用于验证展开行为的长通知。".repeat(8) + "长通知到这里完整结束。";
function notification(id: string, overrides: Partial<AgentNotification> = {}): AgentNotification {
  return {
    notificationId: id, eventKey: "debug:" + id, kind: "ambient", sessionId: null,
    runId: null, approvalId: null, occurrenceId: null, title: id, text: "通知 " + id,
    generationStatus: "ready", textSource: "provided", version: 3,
    createdAt: "2026-09-06T07:20:00Z", expiresAt: null, navigation: null,
    delivery: {
      channel: "pet", status: "presented", attempt: 1, presentedAt: "2026-09-06T07:20:01Z",
      presentedText: "通知 " + id, truncated: false, dismissedAt: null,
    },
    ...overrides,
  };
}
function dismissed(item: AgentNotification): AgentNotification {
  return { ...item, version: item.version + 1,
    delivery: { ...item.delivery, status: "dismissed", dismissedAt: "2026-09-06T07:21:00Z" } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("PetBubble controlled rows", () => {
  let root: Root;
  let container: HTMLDivElement;
  let onExitComplete: (ids: string[]) => void;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    resetNotificationsForTests();
    usePetBubbleStore.setState({ items: [] });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(console, "debug").mockImplementation(() => {});
    mocks.presentation.mockImplementation((options: { onExitComplete: (ids: string[]) => void }) => {
      onExitComplete = options.onExitComplete;
    });
    mocks.invoke.mockResolvedValue(undefined);
    mocks.snapshot.mockImplementation(async () => ({ notifications: Object.values(useNotificationStore.getState().records) }));
    mocks.dismiss.mockImplementation(async (id: string) => ({
      notification: dismissed(useNotificationStore.getState().records[id]),
    }));
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    resetNotificationsForTests();
    usePetBubbleStore.setState({ items: [] });
    document.body.replaceChildren();
    vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  });

  async function mount(...items: AgentNotification[]) {
    useNotificationStore.setState({
      records: Object.fromEntries(items.map((item) => [item.notificationId, item])), ready: true,
    });
    await act(async () => root.render(createElement(PetBubble)));
  }
  function rows() {
    return [...container.querySelectorAll<HTMLElement>("[data-bubble-item]")];
  }
  function activeIds() {
    return rows().filter((item) => item.dataset.state === "present").map((item) => item.dataset.bubbleId);
  }
  function row(id: string) {
    const result = rows().find((item) => item.dataset.bubbleId === id);
    expect(result).toBeDefined();
    return result!;
  }
  function closeButton(id: string) {
    return row(id).querySelector<HTMLButtonElement>("[data-close-button]")!;
  }
  function text(id: string) {
    return row(id).querySelector<HTMLElement>("[data-truncated]")!;
  }
  function sourceTitle(id: string) {
    return row(id).querySelector<HTMLElement>("[data-notification-source]");
  }
  async function clickOperation(id: string, label: string) {
    const button = [...row(id).querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => candidate.textContent === label);
    expect(button).toBeDefined();
    await act(async () => button!.click());
  }
  async function finishExits(...ids: string[]) {
    await act(async () => onExitComplete(ids));
  }
  function expectNoNewPresentation() {
    expect(mocks.presented).not.toHaveBeenCalled();
    expect(mocks.invoke.mock.calls.filter(([command]) => command === "play_notification_sound")).toEqual([]);
  }

  it.each(["reply_ready", "needs_action", "run_failed", "run_interrupted", "ambient"] as const)(
    "shows the frozen session name separately from %s notification text", async (kind) => {
      await mount(notification("named", {
        eventKey: "run:session:run", kind, sessionId: "session", title: "整理书桌",
        text: "那边有新回复了。", navigation: { sessionId: "session" },
      }));
      expect(sourceTitle("named")?.textContent).toBe("整理书桌");
      expect(text("named").textContent).toBe("那边有新回复了。");
      expect(sourceTitle("named")?.closest("[data-notification-id]")).toBeNull();
      expect(sourceTitle("named")?.parentElement).toBe(text("named").parentElement);
      expect(text("named").parentElement?.hasAttribute("data-notification-content")).toBe(true);
      expectNoNewPresentation();
    },
  );

  it("labels Todo sources as tasks and leaves source-free debug and legacy rows unlabelled", async () => {
    usePetBubbleStore.setState({ items: [
      { id: "legacy", text: "旧提醒正文", sourceKind: "ambient", expanded: false },
    ] });
    await mount(
      notification("todo", { eventKey: "todo:occurrence", kind: "todo_due", sessionId: "session", title: "喝点水" }),
      notification("debug", { title: "气泡调试" }),
      notification("unnamed", { eventKey: "run:session:run", kind: "reply_ready", sessionId: "session", title: "" }),
    );
    expect(sourceTitle("todo")?.textContent).toBe("待办 · 喝点水");
    expect(sourceTitle("debug")).toBeNull();
    expect(sourceTitle("legacy")).toBeNull();
    expect(sourceTitle("unnamed")).toBeNull();
  });

  it("sends only the generated body in the presentation receipt of a named notification", async () => {
    const item = notification("named", {
      eventKey: "run:session:run", kind: "reply_ready", sessionId: "session", title: "整理书桌",
      text: "有新回复了。", delivery: {
        channel: "pet", status: "prepared", attempt: 1, presentedAt: null,
        presentedText: null, truncated: false, dismissedAt: null,
      },
    });
    useNotificationStore.setState({ ownedAttempts: { named: 1 } });
    mocks.presented.mockResolvedValue({ notification: { ...item, version: 4,
      delivery: { ...item.delivery, status: "presented", presentedAt: "2026-09-06T07:20:01Z", presentedText: item.text } },
    playSound: false });
    await mount(item);
    const existing = row("named");
    await act(async () => recordNotificationPresented("named", text("named").textContent!, false));
    expect(mocks.presented).toHaveBeenCalledExactlyOnceWith("named", 1, "有新回复了。", false);
    expect(row("named")).toBe(existing);
    expect(sourceTitle("named")?.textContent).toBe("整理书桌");
    expect(useNotificationStore.getState().records.named.delivery.presentedText).toBe("有新回复了。");
  });

  it("fills the fifth item in authoritative order while retaining the outgoing DOM", async () => {
    const all = ["A", "B", "C", "D", "E"].map((id) => notification(id));
    const pending = deferred<{ notification: AgentNotification }>();
    mocks.dismiss.mockReturnValue(pending.promise);
    await mount(...all);
    expect(activeIds()).toEqual(["A", "B", "C", "D"]);
    const a = row("A"), b = row("B"), c = row("C"), d = row("D");
    closeButton("B").focus();
    await act(async () => closeButton("B").click());
    expect(activeIds()).toEqual(["A", "C", "D", "E"]);
    expect(rows().map((item) => item.dataset.bubbleId)).toEqual(["A", "C", "D", "E", "B"]);
    expect(row("B")).toBe(b);
    expect(row("B").dataset.state).toBe("exiting");
    expect(closeButton("B").disabled).toBe(true);
    expect(document.activeElement).toBe(closeButton("C"));
    expect(row("A")).toBe(a); expect(row("C")).toBe(c); expect(row("D")).toBe(d);
    await act(async () => pending.resolve({ notification: dismissed(all[1]) }));
    expect(row("B").dataset.state).toBe("exiting");
    await finishExits("B");
    expect(rows().map((item) => item.dataset.bubbleId)).toEqual(["A", "C", "D", "E"]);
    expect(mocks.dismiss).toHaveBeenCalledExactlyOnceWith("B", 3);
    expectNoNewPresentation();
  });

  it("does not resurrect a closing snapshot from a newer active backend revision", async () => {
    const item = notification("A", { eventKey: "run:session:run", kind: "reply_ready", sessionId: "session", title: "整理书桌" });
    const pending = deferred<{ notification: AgentNotification }>();
    mocks.dismiss.mockReturnValue(pending.promise);
    await mount(item, notification("B"));
    const existing = row("A"), existingTitle = sourceTitle("A");
    await act(async () => closeButton("A").click());
    await act(async () => useNotificationStore.getState().merge([{ ...item, version: 4, title: "新的来源名", text: "较新的活动投影" }]));
    expect(row("A").dataset.state).toBe("exiting");
    expect(row("A")).toBe(existing);
    expect(sourceTitle("A")).toBe(existingTitle);
    expect(sourceTitle("A")?.textContent).toBe("整理书桌");
    expect(text("A").textContent).toBe(item.text);
    await finishExits("A");
    await act(async () => useNotificationStore.getState().merge([{ ...item, version: 5 }]));
    expect(activeIds()).toEqual(["B"]);
    expect(rows().some((entry) => entry.dataset.bubbleId === "A")).toBe(false);
    await act(async () => pending.resolve({ notification: dismissed({ ...item, version: 5 }) }));
    expect(activeIds()).toEqual(["B"]);
  });

  it("closes through the store and acknowledges the newly rendered fifth row without a sound", async () => {
    const queued = notification("E");
    queued.delivery = { ...queued.delivery, status: "pending", attempt: 0, presentedAt: null, presentedText: null };
    mocks.prepare.mockImplementation(async (id: string) => {
      const item = useNotificationStore.getState().records[id];
      const claimed = { ...item, version: item.version + 1,
        delivery: { ...item.delivery, status: "prepared", attempt: 1 } };
      return { notification: claimed, attempt: 1, text: item.text, channel: "pet", navigation: null };
    });
    mocks.presented.mockImplementation(async (id: string) => {
      const item = useNotificationStore.getState().records[id];
      return { notification: { ...item, version: item.version + 1,
        delivery: { ...item.delivery, status: "presented", presentedAt: "2026-09-06T07:21:01Z" } },
      playSound: true };
    });
    await mount(...["A", "B", "C", "D"].map((id) => notification(id)), queued);
    expect(activeIds()).toEqual(["A", "B", "C", "D"]);
    expect(mocks.prepare).not.toHaveBeenCalled();

    await act(async () => closeButton("A").click());
    expect(mocks.dismiss).toHaveBeenCalledExactlyOnceWith("A", 3, ["E"]);
    expect(mocks.prepare).toHaveBeenCalledExactlyOnceWith("E", 3, expect.any(String));
    expect(activeIds()).toEqual(["B", "C", "D", "E"]);
    expect(row("A").dataset.state).toBe("exiting");
    await finishExits("A");
    await act(async () => recordNotificationPresented("E", text("E").textContent!, false));

    expect(mocks.presented).toHaveBeenCalledExactlyOnceWith("E", 1, "通知 E", false);
    expect(useNotificationStore.getState().records.E.delivery.status).toBe("presented");
    expect(mocks.invoke.mock.calls.filter(([command]) => command === "play_notification_sound")).toEqual([]);
    expect(activeIds()).toEqual(["B", "C", "D", "E"]);
  });

  it("restores an active notification after a failed close even if its exit already finished", async () => {
    const pending = deferred<{ notification: AgentNotification }>();
    mocks.dismiss.mockReturnValue(pending.promise);
    await mount(notification("A"), notification("B"));
    await act(async () => closeButton("A").click());
    await finishExits("A");
    expect(activeIds()).toEqual(["B"]);
    await act(async () => pending.reject(new Error("offline")));
    expect(activeIds()).toEqual(["A", "B"]);
    expect(closeButton("A").disabled).toBe(false);
    expect(useNotificationStore.getState().records.A.delivery.status).toBe("presented");
    await finishExits("A"); // A stale animation callback must not delete a restored row.
    expect(activeIds()).toEqual(["A", "B"]);
    expectNoNewPresentation();
  });

  it("keeps surviving node identity when text or priority order changes", async () => {
    const first = notification("A");
    await mount(first, notification("B"));
    const a = row("A"), b = row("B");
    await act(async () => useNotificationStore.getState().merge([
      { ...first, version: 4, text: "更新的同一条提醒" }, notification("Z", { kind: "needs_action" }),
    ]));
    expect(activeIds()).toEqual(["Z", "A", "B"]);
    expect(row("A")).toBe(a); expect(row("B")).toBe(b);
    expect(text("A").textContent).toBe("更新的同一条提醒");
    expect(mocks.dismiss).not.toHaveBeenCalled();
    expectNoNewPresentation();
  });

  it("expands Unicode text without closing or changing the presentation receipt", async () => {
    const fullText = "🙂".repeat(121) + "完整结束";
    const item = notification("A", { text: fullText });
    await mount(item);
    const original = row("A");
    expect(text("A").textContent).toBe("🙂".repeat(120) + "…");
    expect(text("A").dataset.truncated).toBe("true");
    await clickOperation("A", "展开");
    expect(row("A")).toBe(original);
    expect(text("A").textContent).toBe(fullText);
    expect(text("A").dataset.truncated).toBe("false");
    expect(useNotificationStore.getState().records.A).toEqual(item);
    expect(mocks.dismiss).not.toHaveBeenCalled();
    expectNoNewPresentation();
  });

  it("keeps navigation and expansion non-closing and preserves the ledger record", async () => {
    const item = notification("A", { text: LONG_TEXT, navigation: { sessionId: "session-1" } });
    await mount(item);
    const original = row("A");
    await clickOperation("A", "查看");
    expect(mocks.invoke).toHaveBeenCalledWith("open_chat_session", { sessionId: "session-1" });
    await clickOperation("A", "展开");
    expect(row("A")).toBe(original);
    expect(text("A").textContent).toBe(LONG_TEXT);
    expect(useNotificationStore.getState().records.A).toEqual(item);
    expect(mocks.dismiss).not.toHaveBeenCalled();
    expectNoNewPresentation();
  });

  it("keeps legacy rows expandable and closes only the selected legacy row", async () => {
    usePetBubbleStore.setState({ items: [
      { id: "legacy-A", text: "旧提醒", sourceKind: "todo_reminder", expanded: false },
      { id: "legacy-B", text: LONG_TEXT, sourceKind: "todo_reminder", expanded: false },
    ] });
    await mount();
    expect(activeIds()).toEqual(["legacy-A", "legacy-B"]);
    const survivor = row("legacy-B");
    await clickOperation("legacy-B", "展开");
    expect(text("legacy-B").textContent).toBe(LONG_TEXT);
    await act(async () => closeButton("legacy-A").click());
    expect(row("legacy-A").dataset.state).toBe("exiting");
    expect(usePetBubbleStore.getState().items.map((item) => item.id)).toEqual(["legacy-B"]);
    await finishExits("legacy-A");
    expect(activeIds()).toEqual(["legacy-B"]);
    expect(row("legacy-B")).toBe(survivor);
    expect(mocks.dismiss).not.toHaveBeenCalled();
    expectNoNewPresentation();
  });

  it("keeps prepared rows out of the DOM until the current window owns their attempt", async () => {
    const item = notification("A");
    const prepared: AgentNotification = { ...item, delivery: { ...item.delivery, status: "prepared", attempt: 7 } };
    const pending = deferred<{ notification: AgentNotification; attempt: number; text: string; channel: string; navigation: null }>();
    mocks.prepare.mockReturnValue(pending.promise);
    await mount(prepared);
    expect(activeIds()).toEqual([]);
    await act(async () => pending.resolve({ notification: prepared, attempt: 7, text: item.text, channel: "pet", navigation: null }));
    expect(activeIds()).toEqual(["A"]);
    expect(useNotificationStore.getState().ownedAttempts.A).toBe(7);
    expectNoNewPresentation();
  });

  it("moves focus to the notification region when the last active row closes", async () => {
    await mount(notification("A"));
    closeButton("A").focus();
    await act(async () => closeButton("A").click());
    const viewport = container.querySelector("[data-bubble-viewport]");
    expect(document.activeElement).toBe(viewport);
    expect(viewport?.getAttribute("aria-live")).toBe("polite");
    expect(row("A").dataset.state).toBe("exiting");
    await finishExits("A");
    expect(rows()).toEqual([]);
  });

  it("does not expire persistent rows or silently activate the queued fifth item", async () => {
    await mount(...["A", "B", "C", "D", "E"].map((id) => notification(id)));
    const first = row("A");
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(activeIds()).toEqual(["A", "B", "C", "D"]);
    expect(row("A")).toBe(first);
    expect(mocks.dismiss).not.toHaveBeenCalled();
    expectNoNewPresentation();
  });
});
