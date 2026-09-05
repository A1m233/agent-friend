import { beforeEach, describe, expect, it, vi } from "vitest";

const browserGlobals = vi.hoisted(() => {
  const target = new EventTarget();
  class TestCustomEvent<T = unknown> extends Event {
    detail: T;

    constructor(type: string, init?: { detail?: T }) {
      super(type);
      this.detail = init?.detail as T;
    }
  }
  const testWindow = {
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target),
  };

  globalThis.window = testWindow as unknown as Window & typeof globalThis;
  globalThis.CustomEvent = TestCustomEvent as unknown as typeof CustomEvent;

  return { testWindow };
});

vi.mock("@/services", () => ({
  imApi: {
    listProviders: vi.fn(),
  },
  sessionsApi: {
    list: vi.fn(),
    rename: vi.fn(),
    archive: vi.fn(),
    unarchive: vi.fn(),
    delete: vi.fn(),
    deleteArchived: vi.fn(),
  },
}));
vi.mock("@tauri-apps/api/event", () => ({
  emit: vi.fn(() => Promise.resolve()),
  listen: vi.fn(),
}));
vi.mock("@/utils/tauri", () => ({
  isTauri: () => false,
}));

import { imApi, sessionsApi } from "@/services";
import type { SessionSummary } from "@/types/meta";
import {
  collectBoundIMTypesBySession,
  emitSessionsChanged,
  startSessionsChangedSubscriber,
  useArchivedSessionsStore,
  useSessionsStore,
} from "./sessions";

const mockList = vi.mocked(sessionsApi.list);
const mockListProviders = vi.mocked(imApi.listProviders);
const mockDelete = vi.mocked(sessionsApi.delete);
const mockDeleteArchived = vi.mocked(sessionsApi.deleteArchived);
const mockUnarchive = vi.mocked(sessionsApi.unarchive);

function summary(sessionId: string): SessionSummary {
  return {
    session_id: sessionId,
    title: sessionId,
    created_at: "2026-06-26T00:00:00Z",
    updated_at: "2026-06-26T00:00:00Z",
    persona: "default",
    model: "test",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListProviders.mockResolvedValue([]);
  useSessionsStore.setState({
    list: [],
    imTypesBySession: {},
    loading: false,
    loaded: false,
    error: false,
  });
  useArchivedSessionsStore.setState({
    list: [],
    query: "",
    loading: false,
    loaded: false,
    pendingIds: [],
    deletingAll: false,
  });
});

describe("sessions store", () => {
  it("marks the list as loaded only after a successful refresh", async () => {
    const request = deferred<SessionSummary[]>();
    mockList.mockReturnValueOnce(request.promise);

    const refresh = useSessionsStore.getState().refresh();
    expect(useSessionsStore.getState()).toMatchObject({
      list: [], loading: true, loaded: false, error: false,
    });

    request.resolve([summary("sid-1")]);
    await refresh;
    expect(useSessionsStore.getState()).toMatchObject({
      list: [summary("sid-1")], loading: false, loaded: true, error: false,
    });
  });

  it("recognizes a successfully loaded empty list", async () => {
    mockList.mockResolvedValueOnce([]);

    await useSessionsStore.getState().refresh();

    expect(useSessionsStore.getState()).toMatchObject({
      list: [], loading: false, loaded: true, error: false,
    });
  });

  it("keeps an initial failure distinct from an empty list and clears it on retry", async () => {
    mockList.mockRejectedValueOnce(new Error("offline"));

    await useSessionsStore.getState().refresh();
    expect(useSessionsStore.getState()).toMatchObject({
      list: [], loading: false, loaded: false, error: true,
    });

    const retry = deferred<SessionSummary[]>();
    mockList.mockReturnValueOnce(retry.promise);
    const refresh = useSessionsStore.getState().refresh();
    expect(useSessionsStore.getState()).toMatchObject({
      list: [], loading: true, loaded: false, error: false,
    });

    retry.resolve([summary("sid-1")]);
    await refresh;
    expect(useSessionsStore.getState()).toMatchObject({
      list: [summary("sid-1")], loading: false, loaded: true, error: false,
    });
  });

  it("projects bound IM types into the session list cache", async () => {
    mockList.mockResolvedValue([summary("sid-1")]);
    mockListProviders.mockResolvedValue([
      {
        im_type: "qq",
        bind_id: "bind-1",
        bind_id_masked: "bind…1",
        status: "active",
        binding_status: "bound",
        session_id: "sid-1",
        controls_capability: "supported",
      },
    ]);

    await useSessionsStore.getState().refresh();

    expect(useSessionsStore.getState().imTypesBySession).toEqual({ "sid-1": ["qq"] });
  });

  it("ignores unbound providers and deduplicates the same IM type", () => {
    expect(
      collectBoundIMTypesBySession([
        {
          im_type: "qq",
          bind_id: "a",
          bind_id_masked: "a",
          status: "active",
          binding_status: "bound",
          session_id: "sid-1",
          controls_capability: "supported",
        },
        {
          im_type: "qq",
          bind_id: "b",
          bind_id_masked: "b",
          status: "active",
          binding_status: "bound",
          session_id: "sid-1",
          controls_capability: "supported",
        },
        {
          im_type: "qq",
          bind_id: "c",
          bind_id_masked: "c",
          status: "active",
          binding_status: "unbound",
          session_id: null,
          controls_capability: "supported",
        },
      ]),
    ).toEqual({ "sid-1": ["qq"] });
  });

  it("keeps the loaded list and IM labels while refreshing and after failure", async () => {
    const cached = {
      list: [summary("sid-old")],
      imTypesBySession: { "sid-old": ["qq" as const] },
      loaded: true,
    };
    useSessionsStore.setState(cached);
    const request = deferred<SessionSummary[]>();
    mockList.mockReturnValueOnce(request.promise);

    const refresh = useSessionsStore.getState().refresh();
    expect(useSessionsStore.getState()).toMatchObject({
      ...cached, loading: true, error: false,
    });

    request.reject(new Error("offline"));
    await refresh;
    expect(useSessionsStore.getState()).toMatchObject({
      ...cached, loading: false, error: true,
    });
  });

  it.each(["success", "failure"] as const)(
    "ignores an obsolete %s while the latest request is still loading",
    async (outcome) => {
      const earlier = deferred<SessionSummary[]>();
      const latest = deferred<SessionSummary[]>();
      mockList.mockReturnValueOnce(earlier.promise).mockReturnValueOnce(latest.promise);

      const earlierRefresh = useSessionsStore.getState().refresh();
      const latestRefresh = useSessionsStore.getState().refresh();
      if (outcome === "success") earlier.resolve([summary("obsolete")]);
      else earlier.reject(new Error("obsolete failure"));
      await earlierRefresh;
      expect(useSessionsStore.getState()).toMatchObject({
        list: [], loading: true, loaded: false, error: false,
      });

      latest.resolve([summary("latest")]);
      await latestRefresh;
      expect(useSessionsStore.getState()).toMatchObject({
        list: [summary("latest")], loading: false, loaded: true, error: false,
      });
    },
  );

  it("does not let an obsolete success clear the latest failure", async () => {
    const earlier = deferred<SessionSummary[]>();
    mockList.mockReturnValueOnce(earlier.promise).mockRejectedValueOnce(new Error("latest failure"));

    const earlierRefresh = useSessionsStore.getState().refresh();
    await useSessionsStore.getState().refresh();
    expect(useSessionsStore.getState()).toMatchObject({
      list: [], loading: false, loaded: false, error: true,
    });

    earlier.resolve([summary("obsolete")]);
    await earlierRefresh;
    expect(useSessionsStore.getState()).toMatchObject({
      list: [], loading: false, loaded: false, error: true,
    });
  });

  it("does not let an obsolete failure replace the latest successful list", async () => {
    const earlier = deferred<SessionSummary[]>();
    mockList.mockReturnValueOnce(earlier.promise).mockResolvedValueOnce([summary("latest")]);

    const earlierRefresh = useSessionsStore.getState().refresh();
    await useSessionsStore.getState().refresh();
    earlier.reject(new Error("obsolete failure"));
    await earlierRefresh;

    expect(useSessionsStore.getState()).toMatchObject({
      list: [summary("latest")], loading: false, loaded: true, error: false,
    });
  });

  it("refreshes loaded session caches when another window emits sessions changed", async () => {
    useSessionsStore.setState({ list: [summary("sid-old")], loading: false, loaded: true });
    useArchivedSessionsStore.setState({
      list: [summary("sid-archived-old")],
      query: "codex",
      loading: false,
      loaded: true,
      pendingIds: [],
      deletingAll: false,
    });
    mockList.mockImplementation(async (options?: { archived?: boolean }) =>
      options?.archived ? [summary("sid-archived-new")] : [summary("sid-new")],
    );
    startSessionsChangedSubscriber();

    browserGlobals.testWindow.dispatchEvent(
      new CustomEvent("sessions://changed", {
        detail: { originId: "external-window", action: "archive" },
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockList).toHaveBeenCalledWith();
    expect(mockList).toHaveBeenCalledWith({ archived: true, q: "codex" });
    expect(useSessionsStore.getState().list.map((s) => s.session_id)).toEqual(["sid-new"]);
    expect(useArchivedSessionsStore.getState().list.map((s) => s.session_id)).toEqual([
      "sid-archived-new",
    ]);
  });

  it("does not refresh again for sessions changed events emitted by the same window", async () => {
    useSessionsStore.setState({ list: [summary("sid-old")], loading: false, loaded: true });
    mockList.mockResolvedValue([summary("sid-new")]);
    startSessionsChangedSubscriber();

    await emitSessionsChanged({ action: "archive", sessionId: "sid-1" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockList).not.toHaveBeenCalled();
  });
});

describe("archived sessions store", () => {
  it("asks backend for archived list with search query", async () => {
    useArchivedSessionsStore.getState().setQuery("codex");
    mockList.mockResolvedValue([summary("sid-archived")]);

    await useArchivedSessionsStore.getState().refresh();

    expect(mockList).toHaveBeenCalledWith({ archived: true, q: "codex" });
    expect(useArchivedSessionsStore.getState().list.map((s) => s.session_id)).toEqual([
      "sid-archived",
    ]);
    expect(useArchivedSessionsStore.getState().loaded).toBe(true);
  });

  it("unarchives then refreshes archived and normal lists", async () => {
    mockUnarchive.mockResolvedValue(summary("sid-1"));
    mockList.mockResolvedValue([]);

    const ok = await useArchivedSessionsStore.getState().unarchive("sid-1");

    expect(ok).toBe(true);
    expect(mockUnarchive).toHaveBeenCalledWith("sid-1");
    expect(mockList).toHaveBeenCalledWith({ archived: true, q: "" });
    expect(mockList).toHaveBeenCalledWith();
    expect(useArchivedSessionsStore.getState().pendingIds).toEqual([]);
  });

  it("deletes one archived session then refreshes lists", async () => {
    mockDelete.mockResolvedValue({ session_id: "sid-1", deleted: true });
    mockList.mockResolvedValue([]);

    const ok = await useArchivedSessionsStore.getState().remove("sid-1");

    expect(ok).toBe(true);
    expect(mockDelete).toHaveBeenCalledWith("sid-1");
    expect(mockList).toHaveBeenCalledWith({ archived: true, q: "" });
    expect(mockList).toHaveBeenCalledWith();
  });

  it("deletes all archived sessions through backend endpoint", async () => {
    mockDeleteArchived.mockResolvedValue({ deleted: 2, session_ids: ["a", "b"] });
    mockList.mockResolvedValue([]);

    const ok = await useArchivedSessionsStore.getState().removeAll();

    expect(ok).toBe(true);
    expect(mockDeleteArchived).toHaveBeenCalledWith();
    expect(useArchivedSessionsStore.getState().deletingAll).toBe(false);
  });
});
