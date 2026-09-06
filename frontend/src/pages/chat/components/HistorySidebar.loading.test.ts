// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SidebarProvider } from "@/components/ui";
import { imApi, sessionsApi, type ProviderInfo } from "@/services";
import { useConnectionStore, useSessionsStore } from "@/stores";
import type { SessionSummary } from "@/types/meta";
import { HistorySidebar } from "./HistorySidebar";
import { HistorySidebarStatus } from "./HistorySidebarStatus";

vi.mock("kbar", () => ({ useKBar: () => ({ query: { toggle: vi.fn() } }) }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => false }));
vi.mock("@tauri-apps/api/event", () => ({
  emit: vi.fn(async () => {}),
  listen: vi.fn(async () => () => {}),
}));
vi.mock("@/lib/persistence/chatUi", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/persistence/chatUi")>(),
  getChatUiPersistence: vi.fn(async () => ({ lastChatSessionId: null })),
  setLastChatSessionId: vi.fn(async () => {}),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function session(): SessionSummary {
  return {
    session_id: "saved-session",
    title: "之前保存的对话",
    created_at: "2026-09-06T00:00:00Z",
    updated_at: "2026-09-06T00:00:00Z",
    persona: "default",
    model: "test",
  };
}

describe("HistorySidebar session loading", () => {
  let container: HTMLDivElement;
  let root: Root;
  let initialSessions: ReturnType<typeof useSessionsStore.getState>;
  let initialConnection: ReturnType<typeof useConnectionStore.getState>;
  let listRequest: ReturnType<typeof deferred<SessionSummary[]>>;

  beforeEach(() => {
    initialSessions = useSessionsStore.getState();
    initialConnection = useConnectionStore.getState();
    useSessionsStore.setState({
      list: [], imTypesBySession: {}, loaded: false, loading: false, error: false,
    });
    useConnectionStore.setState({ status: "unknown" });
    listRequest = deferred<SessionSummary[]>();
    vi.spyOn(sessionsApi, "list").mockReturnValue(listRequest.promise);
    vi.spyOn(imApi, "listProviders").mockResolvedValue([]);
    // jsdom supplies no media queries or layout observers; the sidebar,
    // store refresh, status component and user controls all remain real.
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useSessionsStore.setState(initialSessions, true);
    useConnectionStore.setState(initialConnection, true);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function mount() {
    await act(async () => {
      root.render(createElement(SidebarProvider, null, createElement(HistorySidebar, {
        memoryInspectorEnabled: false,
        onNewConversation: vi.fn(),
        blockAutoRestore: vi.fn(),
        isAutoRestoreBlocked: () => false,
      })));
    });
  }

  function skeletons() {
    return container.querySelectorAll('[data-slot="sidebar-menu-skeleton"]');
  }

  function expectLoading() {
    const loadingState = container.querySelector('[role="status"]');
    expect(loadingState?.getAttribute("aria-label")).toBe("正在加载会话");
    expect(loadingState?.textContent).toBe("");
    expect(skeletons()).toHaveLength(3);
    for (const skeleton of skeletons()) {
      expect(skeleton.classList.contains("*:bg-surface-hover")).toBe(true);
    }
    expect(container.textContent).not.toContain("还没有历史会话");
  }

  it("keeps a placeholder from connecting through an unfinished first list request", async () => {
    await mount();

    expectLoading();
    expect(sessionsApi.list).not.toHaveBeenCalled();

    await act(async () => useConnectionStore.setState({ status: "online" }));

    expect(sessionsApi.list).toHaveBeenCalledOnce();
    expectLoading();

    await act(async () => listRequest.resolve([session()]));

    expect(container.textContent).toContain("之前保存的对话");
    expect(skeletons()).toHaveLength(0);
    expect(container.textContent).not.toContain("正在加载会话");
    expect(container.textContent).not.toContain("还没有历史会话");
  });

  it("does not flash an empty history on the online render before refresh starts", () => {
    const html = renderToStaticMarkup(createElement(HistorySidebarStatus, {
      status: "online", loaded: false, loading: false, error: false, onRetry: vi.fn(),
    }));

    const markup = document.createElement("template");
    markup.innerHTML = html;
    expect(markup.content.textContent).toBe("");
    expect(html).toContain('aria-label="正在加载会话"');
    expect(html).not.toContain("还没有历史会话");
    expect(html).not.toContain("会话列表加载失败");
  });

  it("shows empty history only after the first request succeeds with no sessions", async () => {
    useConnectionStore.setState({ status: "online" });
    await mount();
    expectLoading();

    await act(async () => listRequest.resolve([]));

    expect(container.textContent).toContain("还没有历史会话");
    expect(skeletons()).toHaveLength(0);
    expect(container.textContent).not.toContain("正在加载会话");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("retries a failed initial load through the visible retry button", async () => {
    useConnectionStore.setState({ status: "online" });
    await mount();
    await act(async () => listRequest.reject(new Error("request failed")));

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("会话列表加载失败");
    expect(container.textContent).not.toContain("还没有历史会话");
    expect(skeletons()).toHaveLength(0);
    const retry = alert?.querySelector<HTMLButtonElement>("button");
    expect(retry?.textContent).toBe("重试");

    const nextRequest = deferred<SessionSummary[]>();
    vi.mocked(sessionsApi.list).mockReturnValue(nextRequest.promise);
    await act(async () => retry!.click());

    expect(sessionsApi.list).toHaveBeenCalledTimes(2);
    expectLoading();
    expect(container.querySelector('[role="alert"]')).toBeNull();

    await act(async () => nextRequest.resolve([session()]));

    expect(container.textContent).toContain("之前保存的对话");
    expect(skeletons()).toHaveLength(0);
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("preserves existing session rows while a background refresh waits, fails and disconnects", async () => {
    useConnectionStore.setState({ status: "online" });
    await mount();
    await act(async () => listRequest.resolve([session()]));

    const backgroundRequest = deferred<SessionSummary[]>();
    vi.mocked(sessionsApi.list).mockReturnValue(backgroundRequest.promise);
    await act(async () => { window.dispatchEvent(new Event("focus")); });

    expect(sessionsApi.list).toHaveBeenCalledTimes(2);
    expect(useSessionsStore.getState().loading).toBe(true);
    expect(container.textContent).toContain("之前保存的对话");
    expect(skeletons()).toHaveLength(0);
    expect(container.textContent).not.toContain("正在加载会话");

    await act(async () => backgroundRequest.reject(new Error("connection lost")));

    expect(useSessionsStore.getState().error).toBe(true);
    expect(container.textContent).toContain("之前保存的对话");
    expect(container.querySelector('[role="alert"]')).toBeNull();

    await act(async () => useConnectionStore.setState({ status: "offline" }));

    expect(container.textContent).toContain("之前保存的对话");
    expect(container.textContent).not.toContain("连接恢复后将加载会话");
    expect(container.textContent).not.toContain("还没有历史会话");
  });

  it("explains an offline first load and resumes loading after reconnecting", async () => {
    useConnectionStore.setState({ status: "offline" });
    await mount();

    expect(container.querySelector('[role="status"]')?.textContent).toBe("连接恢复后将加载会话");
    expect(skeletons()).toHaveLength(0);
    expect(container.textContent).not.toContain("还没有历史会话");
    expect(sessionsApi.list).not.toHaveBeenCalled();

    await act(async () => useConnectionStore.setState({ status: "online" }));
    expectLoading();
    await act(async () => listRequest.resolve([session()]));
    expect(container.textContent).toContain("之前保存的对话");
  });

  it("keeps loading visible until the IM labels in the same refresh are ready", async () => {
    const providers = deferred<ProviderInfo[]>();
    vi.mocked(imApi.listProviders).mockReturnValue(providers.promise);
    useConnectionStore.setState({ status: "online" });
    await mount();
    await act(async () => listRequest.resolve([session()]));

    expectLoading();
    expect(container.textContent).not.toContain("之前保存的对话");

    await act(async () => providers.resolve([]));

    expect(container.textContent).toContain("之前保存的对话");
    expect(skeletons()).toHaveLength(0);
    expect(container.textContent).not.toContain("正在加载会话");
  });
});
