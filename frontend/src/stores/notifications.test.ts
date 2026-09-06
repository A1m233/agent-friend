import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AgentNotification,
  NotificationPresentationReceipt,
  PreparedNotification,
} from "@/types/notification";

const mocks = vi.hoisted(() => ({
  snapshot: vi.fn(),
  prepare: vi.fn(),
  presented: vi.fn(),
  dismiss: vi.fn(),
  invoke: vi.fn(),
  listen: vi.fn(),
  isTauri: vi.fn(() => true),
}));

vi.mock("@/services/api/notifications", () => ({ notificationsApi: mocks }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/utils/tauri", () => ({ isTauri: mocks.isTauri }));

import {
  dismissNotification,
  prepareNotification,
  recordNotificationPresented,
  refreshNotifications,
  resetNotificationsForTests,
  selectBubbleNotifications,
  startNotificationSubscriber,
  useNotificationStore,
} from "./notifications";

type Changes = Omit<Partial<AgentNotification>, "delivery"> & {
  delivery?: Partial<AgentNotification["delivery"]>;
};

function notification(id = "n1", changes: Changes = {}): AgentNotification {
  const { delivery, ...other } = changes;
  return {
    notificationId: id,
    eventKey: `reply_ready:${id}`,
    kind: "reply_ready",
    sessionId: "s1",
    runId: "r1",
    approvalId: null,
    occurrenceId: null,
    title: "整理照片",
    text: "照片整理好了，回来看看吧。",
    generationStatus: "ready",
    textSource: "generated",
    version: 1,
    createdAt: "2026-09-06T07:20:00Z",
    expiresAt: null,
    navigation: { sessionId: "s1" },
    ...other,
    delivery: {
      channel: "pet",
      status: "pending",
      attempt: 0,
      presentedAt: null,
      presentedText: null,
      truncated: false,
      dismissedAt: null,
      ...delivery,
    },
  };
}

function prepared(item: AgentNotification, attempt = 1): PreparedNotification {
  return {
    notification: { ...item, version: item.version + 1,
      delivery: { ...item.delivery, status: "prepared", attempt } },
    attempt,
    text: item.text,
    channel: "pet",
    navigation: item.navigation,
  };
}

function receipt(item: AgentNotification, playSound = true): NotificationPresentationReceipt {
  return { notification: { ...item, version: item.version + 1,
    delivery: { ...item.delivery, status: "presented", presentedAt: "2026-09-06T07:20:02Z" } }, playSound };
}

function dismissed(item: AgentNotification): AgentNotification {
  return { ...item, version: item.version + 1,
    delivery: { ...item.delivery, status: "dismissed", dismissedAt: "2026-09-06T07:21:00Z" } };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function ready(...items: AgentNotification[]): void {
  useNotificationStore.getState().merge(items);
  useNotificationStore.setState({ ready: true });
}

beforeEach(() => {
  resetNotificationsForTests();
  vi.clearAllMocks();
  mocks.snapshot.mockResolvedValue({ notifications: [] });
  mocks.invoke.mockResolvedValue(undefined);
  mocks.listen.mockResolvedValue(vi.fn());
  mocks.isTauri.mockReturnValue(true);
});

afterEach(() => {
  resetNotificationsForTests();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("notification versions and visible queue", () => {
  it("keeps newer terminal state when snapshots and push events arrive out of order", () => {
    const newest = notification("n1", { version: 4, delivery: { status: "dismissed" } });
    const store = useNotificationStore.getState();
    store.merge([newest]);
    store.merge([notification("n1", { version: 2 }), notification("n1", { version: 4, text: "旧文案" })]);
    store.merge([notification("bad", { version: Number.NaN })]);
    expect(useNotificationStore.getState().records).toEqual({ n1: newest });
    expect(selectBubbleNotifications(useNotificationStore.getState().records)).toEqual([]);
  });

  it("prioritizes actionable ready notifications and leaves overflow outside the stack", () => {
    const items = [
      notification("reply"), notification("todo", { kind: "todo_due" }),
      notification("failed", { kind: "run_failed" }), notification("approval", { kind: "needs_action" }),
      notification("ambient", { kind: "ambient" }),
      notification("generating", { generationStatus: "generating" }),
      notification("expired", { generationStatus: "expired" }),
      notification("invalidated", { delivery: { status: "invalidated" } }),
    ];
    ready(...items);
    expect(selectBubbleNotifications(useNotificationStore.getState().records)
      .map((item) => item.notificationId)).toEqual(["approval", "failed", "todo", "reply"]);
  });

  it("does not prepare or acknowledge before a snapshot establishes readiness", async () => {
    useNotificationStore.getState().merge([notification()]);
    await prepareNotification("n1");
    await recordNotificationPresented("n1", "文本", false);
    expect(mocks.prepare).not.toHaveBeenCalled();
    expect(mocks.presented).not.toHaveBeenCalled();
  });

  it("does not prepare or acknowledge the queued fifth notification", async () => {
    ready(...[1, 2, 3, 4, 5].map((number) => notification(`n${number}`)));
    mocks.prepare.mockImplementation(async (id: string) => prepared(useNotificationStore.getState().records[id]));
    for (const item of selectBubbleNotifications(useNotificationStore.getState().records)) {
      await prepareNotification(item.notificationId);
    }
    await recordNotificationPresented("n5", "排队文本", false);
    expect(mocks.prepare.mock.calls.map(([id]) => id)).toEqual(["n1", "n2", "n3", "n4"]);
    expect(mocks.presented).not.toHaveBeenCalled();
    expect(mocks.invoke).not.toHaveBeenCalled();
  });
});

describe("notification presentation ownership", () => {
  it("coalesces concurrent prepare requests and freezes the returned attempt", async () => {
    const item = notification();
    ready(item);
    const pending = deferred<PreparedNotification>();
    mocks.prepare.mockReturnValue(pending.promise);
    const first = prepareNotification("n1");
    await prepareNotification("n1");
    expect(mocks.prepare).toHaveBeenCalledTimes(1);
    expect(mocks.prepare).toHaveBeenCalledWith("n1", 1, expect.any(String));
    pending.resolve(prepared(item, 3));
    await first;
    await prepareNotification("n1");
    expect(mocks.prepare).toHaveBeenCalledTimes(1);
    expect(useNotificationStore.getState().ownedAttempts.n1).toBe(3);
  });

  it("reclaims an old window's prepared item before sending its presentation receipt", async () => {
    const previous = notification("n1", { version: 5, delivery: { status: "prepared", attempt: 4 } });
    ready(previous);
    await recordNotificationPresented("n1", previous.text, false);
    expect(mocks.presented).not.toHaveBeenCalled();
    const claimed = prepared(previous, 5);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    mocks.presented.mockResolvedValue(receipt(claimed.notification, false));
    await recordNotificationPresented("n1", previous.text, false);
    expect(mocks.prepare).toHaveBeenCalledWith("n1", 5, expect.any(String));
    expect(mocks.presented).toHaveBeenCalledWith("n1", 5, previous.text, false);
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("records only actual displayed truncated text and plays only for the first accepted receipt", async () => {
    const item = notification();
    ready(item);
    const claimed = prepared(item);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    const pending = deferred<NotificationPresentationReceipt>();
    mocks.presented.mockReturnValue(pending.promise);
    const first = recordNotificationPresented("n1", "照片整理…", true);
    await recordNotificationPresented("n1", "照片整理…", true);
    expect(mocks.presented).toHaveBeenCalledTimes(1);
    expect(mocks.presented).toHaveBeenCalledWith("n1", 1, "照片整理…", true);
    expect(mocks.invoke).not.toHaveBeenCalled();
    pending.resolve(receipt(claimed.notification));
    await first;
    await recordNotificationPresented("n1", item.text, false);
    expect(mocks.invoke).toHaveBeenCalledExactlyOnceWith("play_notification_sound", { notificationId: "n1" });
    expect(mocks.presented).toHaveBeenCalledTimes(1);
  });

  it("does not retry a failed native sound after the presented receipt was committed", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const item = notification();
    ready(item);
    const claimed = prepared(item);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    mocks.presented.mockResolvedValue(receipt(claimed.notification));
    mocks.invoke.mockRejectedValueOnce(new Error("audio unavailable"));
    await recordNotificationPresented("n1", item.text, false);
    await recordNotificationPresented("n1", item.text, false);
    expect(mocks.invoke).toHaveBeenCalledTimes(1);
    expect(useNotificationStore.getState().records.n1.delivery.status).toBe("presented");
  });

  it("dismisses once and ignores older snapshots that would reopen the notification", async () => {
    const item = notification("n1", { version: 3, delivery: { status: "presented", attempt: 1 } });
    ready(item);
    const dismissed = notification("n1", { version: 4, delivery: { status: "dismissed", attempt: 1 } });
    const pending = deferred<{ notification: AgentNotification }>();
    mocks.dismiss.mockReturnValue(pending.promise);
    const first = dismissNotification("n1");
    await dismissNotification("n1");
    pending.resolve({ notification: dismissed });
    await first;
    useNotificationStore.getState().merge([item]);
    expect(mocks.dismiss).toHaveBeenCalledExactlyOnceWith("n1", 3);
    expect(selectBubbleNotifications(useNotificationStore.getState().records)).toEqual([]);
  });

  it("discards a presentation response from a previous subscriber lifetime", async () => {
    const item = notification();
    ready(item);
    const claimed = prepared(item);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    const pending = deferred<NotificationPresentationReceipt>();
    mocks.presented.mockReturnValue(pending.promise);
    const operation = recordNotificationPresented("n1", item.text, false);
    resetNotificationsForTests();
    pending.resolve(receipt(claimed.notification));
    await operation;
    expect(useNotificationStore.getState().records).toEqual({});
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("does not sound a stale presentation receipt after a newer dismissal was received", async () => {
    const item = notification();
    ready(item);
    const claimed = prepared(item);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    const pending = deferred<NotificationPresentationReceipt>();
    mocks.presented.mockReturnValue(pending.promise);
    const operation = recordNotificationPresented("n1", item.text, false);
    useNotificationStore.getState().merge([
      notification("n1", { version: 4, delivery: { status: "dismissed", attempt: 1 } }),
    ]);
    pending.resolve(receipt(claimed.notification));
    await operation;
    expect(useNotificationStore.getState().records.n1.delivery.status).toBe("dismissed");
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("does not sound when snapshot readiness was lost while a receipt was in flight", async () => {
    const item = notification();
    ready(item);
    const claimed = prepared(item);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    const pending = deferred<NotificationPresentationReceipt>();
    mocks.presented.mockReturnValue(pending.promise);
    const operation = recordNotificationPresented("n1", item.text, false);
    useNotificationStore.setState({ ready: false });
    pending.resolve(receipt(claimed.notification));
    await operation;
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("does not sound a presented response while the user's close request is still in flight", async () => {
    const item = notification();
    ready(item);
    const claimed = prepared(item);
    mocks.prepare.mockResolvedValue(claimed);
    await prepareNotification("n1");
    const presenting = deferred<NotificationPresentationReceipt>();
    const closing = deferred<{ notification: AgentNotification }>();
    mocks.presented.mockReturnValue(presenting.promise);
    mocks.dismiss.mockReturnValue(closing.promise);
    const presentOperation = recordNotificationPresented("n1", item.text, false);
    const closeOperation = dismissNotification("n1");
    presenting.resolve(receipt(claimed.notification, true));
    await presentOperation;
    expect(mocks.invoke).not.toHaveBeenCalled();
    closing.resolve({ notification: notification("n1", {
      version: 4, delivery: { status: "dismissed", attempt: 1 },
    }) });
    await closeOperation;
    expect(useNotificationStore.getState().records.n1.delivery.status).toBe("dismissed");
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("retries close against the newer presented version after its original version conflicts", async () => {
    vi.spyOn(console, "debug").mockImplementation(() => {});
    const waiting = notification("n1", { version: 2, delivery: { status: "prepared", attempt: 1 } });
    const presented = receipt(waiting, false).notification;
    const dismissed = notification("n1", { version: 4, delivery: { status: "dismissed", attempt: 1 } });
    ready(waiting);
    mocks.dismiss.mockRejectedValueOnce(new Error("409: notification version advanced"))
      .mockResolvedValueOnce({ notification: dismissed });
    mocks.snapshot.mockResolvedValue({ notifications: [presented] });
    await dismissNotification("n1");
    expect(mocks.dismiss.mock.calls).toEqual([["n1", 2], ["n1", 3]]);
    expect(mocks.snapshot).toHaveBeenCalledTimes(1);
    expect(useNotificationStore.getState().records.n1).toEqual(dismissed);
    expect(selectBubbleNotifications(useNotificationStore.getState().records)).toEqual([]);
    expect(mocks.invoke).not.toHaveBeenCalled();
  });
});

describe("notifications that replace a closed row", () => {
  beforeEach(() => {
    mocks.prepare.mockImplementation(async (id: string) => prepared(useNotificationStore.getState().records[id]));
    mocks.presented.mockImplementation(async (id: string) => receipt(useNotificationStore.getState().records[id], true));
    mocks.dismiss.mockImplementation(async (id: string) => ({
      notification: dismissed(useNotificationStore.getState().records[id]),
    }));
  });

  it("silences only the fifth replacement while still acknowledging every presented row", async () => {
    ready(...["A", "B", "C", "D", "E"].map((id) => notification(id)));
    for (const id of ["A", "B", "C", "D"]) await prepareNotification(id);

    await dismissNotification("A");
    expect(mocks.dismiss).toHaveBeenCalledExactlyOnceWith("A", 2, ["E"]);
    await prepareNotification("E");
    for (const id of ["B", "C", "D", "E"]) {
      await recordNotificationPresented(id, `实际显示 ${id}`, false);
    }

    expect(mocks.presented.mock.calls).toEqual(["B", "C", "D", "E"]
      .map((id) => [id, 1, `实际显示 ${id}`, false]));
    expect(mocks.invoke.mock.calls).toEqual(["B", "C", "D"]
      .map((notificationId) => ["play_notification_sound", { notificationId }]));
    for (const id of ["B", "C", "D", "E"]) {
      expect(useNotificationStore.getState().records[id].delivery.status).toBe("presented");
    }
  });

  it("freezes E and F across consecutive closes without silencing a new higher-priority arrival", async () => {
    ready(...["A", "B", "C", "D", "E", "F"].map((id) => notification(id)));
    const closingA = deferred<{ notification: AgentNotification }>();
    const closingB = deferred<{ notification: AgentNotification }>();
    mocks.dismiss.mockReturnValueOnce(closingA.promise).mockReturnValueOnce(closingB.promise);

    const first = dismissNotification("A");
    const second = dismissNotification("B");
    expect(mocks.dismiss.mock.calls).toEqual([["A", 1, ["E"]], ["B", 1, ["F"]]]);
    useNotificationStore.getState().merge([notification("Z", { kind: "needs_action" })]);
    closingB.resolve({ notification: dismissed(useNotificationStore.getState().records.B) });
    await second;
    closingA.resolve({ notification: dismissed(useNotificationStore.getState().records.A) });
    await first;

    for (const id of ["E", "F", "Z"]) {
      await prepareNotification(id);
      await recordNotificationPresented(id, `实际显示 ${id}`, false);
    }
    expect(mocks.presented.mock.calls.map(([id]) => id)).toEqual(["E", "F", "Z"]);
    expect(mocks.invoke).toHaveBeenCalledExactlyOnceWith("play_notification_sound", { notificationId: "Z" });
  });

  it("keeps a replacement silent when its sound-qualified receipt arrives after close has finished", async () => {
    ready(...["A", "B", "C", "D", "E"].map((id) => notification(id)));
    const closing = deferred<{ notification: AgentNotification }>();
    const presenting = deferred<NotificationPresentationReceipt>();
    mocks.dismiss.mockReturnValueOnce(closing.promise);
    mocks.presented.mockReturnValueOnce(presenting.promise);

    const closeOperation = dismissNotification("A");
    await prepareNotification("E");
    const claimed = useNotificationStore.getState().records.E;
    const presentOperation = recordNotificationPresented("E", "补位的实际显示文本…", true);
    expect(mocks.presented).toHaveBeenCalledExactlyOnceWith("E", 1, "补位的实际显示文本…", true);
    closing.resolve({ notification: dismissed(useNotificationStore.getState().records.A) });
    await closeOperation;
    expect(useNotificationStore.getState().records.A.delivery.status).toBe("dismissed");

    presenting.resolve(receipt(claimed, true));
    await presentOperation;
    expect(useNotificationStore.getState().records.E.delivery.status).toBe("presented");
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("keeps the original replacement IDs when a version conflict refreshes a reordered queue", async () => {
    vi.spyOn(console, "debug").mockImplementation(() => {});
    ready(...["A", "B", "C", "D", "E"].map((id) => notification(id)));
    const newerA = notification("A", { version: 2 });
    mocks.dismiss.mockRejectedValueOnce(new Error("409: notification version advanced"));
    mocks.snapshot.mockResolvedValueOnce({ notifications: [
      newerA, notification("Z", { kind: "needs_action" }),
    ] });

    await dismissNotification("A");

    expect(mocks.dismiss.mock.calls).toEqual([["A", 1, ["E"]], ["A", 2, ["E"]]]);
    expect(selectBubbleNotifications(useNotificationStore.getState().records)
      .map((item) => item.notificationId)).toEqual(["Z", "B", "C", "D"]);
    for (const id of ["E", "Z", "D"]) {
      await prepareNotification(id);
      await recordNotificationPresented(id, `实际显示 ${id}`, false);
    }
    expect(mocks.presented.mock.calls.map(([id]) => id)).toEqual(["E", "Z", "D"]);
    expect(mocks.invoke.mock.calls).toEqual(["Z", "D"]
      .map((notificationId) => ["play_notification_sound", { notificationId }]));
  });

  it("keeps the two-argument close contract when there is no unseen replacement", async () => {
    ready(...["A", "B", "C", "D"].map((id) => notification(id)),
      notification("E", { delivery: { status: "presented", presentedAt: "2026-09-06T07:20:02Z" } }));
    await dismissNotification("A");
    expect(mocks.dismiss).toHaveBeenCalledExactlyOnceWith("A", 1);
  });
});

describe("notification subscriber initialization", () => {
  it("does not restore an old snapshot after its subscriber has stopped", async () => {
    vi.useFakeTimers();
    const pending = deferred<{ notifications: AgentNotification[] }>();
    mocks.snapshot.mockReturnValue(pending.promise);
    const stop = startNotificationSubscriber();
    for (let tick = 0; tick < 8; tick++) await Promise.resolve();
    expect(mocks.listen.mock.calls.map(([name]) => name)).toEqual([
      "notification://changed", "notification://push-reconnected", "settings://changed",
    ]);
    expect(mocks.snapshot).toHaveBeenCalledTimes(1);
    stop();
    pending.resolve({ notifications: [notification()] });
    await refreshNotifications();
    expect(useNotificationStore.getState().ready).toBe(false);
    expect(useNotificationStore.getState().records).toEqual({});
    expect(mocks.prepare).not.toHaveBeenCalled();
  });
});
