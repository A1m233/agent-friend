// @vitest-environment jsdom
import { act, createElement, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import { useConversationStore, useCommandRuntimeStore, useFilesystemApprovalsStore, useSocialAuthRequestsStore } from "@/stores";
import { usePrimaryActivity } from "./usePrimaryActivity";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => useSocialAuthRequestsStore.getState().clear());

it("mounts the real store adapter and remains stable across unrelated updates", async () => {
  useConversationStore.setState({currentSessionId: "session-test", messages: [], runSnapshots: {}, runCalibrationPhase: "ready", stopRequestsByRun: {}});
  useCommandRuntimeStore.getState().clear();
  useFilesystemApprovalsStore.getState().clear();
  let renders = 0;
  function Consumer() {
    const activity = usePrimaryActivity();
    renders++;
    return createElement("span", null, activity.kind);
  }
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(StrictMode, null, createElement(Consumer))));
    expect(container.textContent).toBe("none");
    expect(renders).toBeLessThan(10);
    await act(async () => useConversationStore.setState({historyLoading: true}));
    expect(renders).toBeLessThan(10);
    await act(async () => useConversationStore.setState({runCalibrationPhase: "calibrating"}));
    expect(container.textContent).toBe("run");
  } finally {
    await act(async () => root.unmount());
  }
});
