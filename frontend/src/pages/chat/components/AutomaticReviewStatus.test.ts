// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useConversationStore, useFilesystemApprovalsStore, useCommandRuntimeStore } from "@/stores";
import type { FilesystemApprovalRequest } from "@/services/api/filesystem";
import type { RunSnapshot } from "@/types/run";
import { AutomaticReviewStatus } from "./AutomaticReviewStatus";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const run: RunSnapshot = {
  run_id: "run-a", session_id: "session-a", status: "running", outcome: null,
  reason_code: "", channel: "text", origin: null, control_scope: null,
  created_at: "", started_at: "", finished_at: null, version: 1,
};
const review: FilesystemApprovalRequest = {
  request_id: "review-a", session_id: "session-a", run_id: "run-a", tool_call_id: "tool-a",
  reason: "读取配置", status: "reviewing", version: 2, items: [], actions: [],
  decision_source: "auto_review",
};

describe("automatic approval presentation", () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    useCommandRuntimeStore.getState().clear();
    useFilesystemApprovalsStore.getState().clear();
    useConversationStore.setState({
      currentSessionId: "session-a", approvalMode: "auto_review", runSnapshots: { "run-a": run },
      readOnlySessionIds: {},
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useFilesystemApprovalsStore.getState().clear();
    useConversationStore.setState({ currentSessionId: null, runSnapshots: {}, readOnlySessionIds: {} });
  });
  async function render(toolCallId = "tool-a") {
    await act(async () => root.render(createElement(AutomaticReviewStatus, { toolCallId })));
  }

  it("keeps automatic review silent and shows only the matching failure in tool details", async () => {
    useFilesystemApprovalsStore.getState().merge(review);
    await render();
    expect(container.textContent).toBe("");
    await act(async () => useFilesystemApprovalsStore.getState().merge({
      ...review, status: "failed", version: 3,
    }));
    await act(async () => useConversationStore.setState({ runSnapshots: {} }));
    await render("tool-a");
    expect(container.textContent).toContain("审批出错");
    expect(container.textContent).toContain("重新审批");
    await render("another-tool");
    expect(container.textContent).toBe("");
  });

  it.each(["disconnect", "finish", "terminal"] as const)(
    "keeps stale reviews silent on %s",
    async (event) => {
      useFilesystemApprovalsStore.getState().merge(review);
      await render();
      expect(container.textContent).toBe("");
      await act(async () => {
        if (event === "disconnect") useFilesystemApprovalsStore.getState().markDisconnected();
        if (event === "finish") useFilesystemApprovalsStore.getState().finishRun("run-a");
        if (event === "terminal") useConversationStore.setState({
          runSnapshots: { "run-a": { ...run, status: "terminal", outcome: "completed" } },
        });
      });
      expect(container.textContent).toBe("");
    },
  );

  it("keeps a stale earlier run from masking the current review", async () => {
    useFilesystemApprovalsStore.getState().merge({ ...review, request_id: "old", run_id: "old-run" });
    useFilesystemApprovalsStore.getState().merge({ ...review, reason: "本轮操作" });
    await render();
    expect(container.textContent).toBe("");
    await act(async () => useConversationStore.setState({ currentSessionId: "other-session" }));
    expect(container.textContent).toBe("");
  });

  it("shows the outcome without manual retry in the read-only inner session", async () => {
    useFilesystemApprovalsStore.getState().merge({ ...review, status: "failed", version: 3 });
    useConversationStore.setState({ runSnapshots: {}, readOnlySessionIds: { "session-a": true } });
    await render("tool-a");
    expect(container.textContent).toContain("审批出错");
    expect(container.textContent).not.toContain("重新审批");
  });
});
