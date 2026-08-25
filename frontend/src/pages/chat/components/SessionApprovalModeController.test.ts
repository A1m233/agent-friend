// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui";
import { sessionsApi } from "@/services";
import {
  useCommandRuntimeStore,
  useConnectionStore,
  useConversationStore,
  useFilesystemApprovalsStore,
} from "@/stores";
import { SessionApprovalModeControl } from "./SessionApprovalModeControl";
import {
  SessionApprovalModeController,
  useSessionApprovalModeActions,
  type SessionAutoApprovalTarget,
} from "./SessionApprovalModeController";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

let capturedActions: ReturnType<typeof useSessionApprovalModeActions> | null = null;

function Probe() {
  capturedActions = useSessionApprovalModeActions();
  return null;
}

function TestSurface() {
  return createElement(
    TooltipProvider,
    null,
    createElement(
      "div",
      null,
      createElement(Probe),
      createElement(SessionApprovalModeControl),
    ),
  );
}

function buttonWithText(text: string): HTMLButtonElement {
  const button = [...document.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent?.includes(text));
  if (!button) throw new Error(`missing button: ${text}`);
  return button;
}

describe("SessionApprovalModeController", () => {
  let container: HTMLDivElement;
  let root: Root;
  const setApprovalMode = vi.fn(async () => ({
    status: "applied" as const,
    mode: "session_auto" as const,
    version: 1,
  }));

  beforeEach(async () => {
    capturedActions = null;
    vi.clearAllMocks();
    useConnectionStore.setState({ status: "online" });
    useConversationStore.setState({
      currentSessionId: "session-a",
      approvalMode: "per_request",
      approvalModeVersion: 0,
      approvalModeChanging: false,
      setApprovalMode,
    });
    useCommandRuntimeStore.setState({
      approvals: {},
      submitting: new Set(),
      refresh: vi.fn(async () => undefined),
    });
    useFilesystemApprovalsStore.setState({
      entries: {},
      refresh: vi.fn(async () => undefined),
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        createElement(
          SessionApprovalModeController,
          null,
          createElement(TestSurface),
        ),
      );
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("requires a fresh confirmation every time the risky mode is entered", async () => {
    if (!capturedActions) throw new Error("actions unavailable");

    await act(async () => capturedActions?.requestSessionAutoFromComposer());
    expect(document.body.textContent).toContain("确认在本会话中免除后续授权？");

    await act(async () => buttonWithText("继续每次授权").click());
    expect(setApprovalMode).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("确认在本会话中免除后续授权？");

    await act(async () => capturedActions?.requestSessionAutoFromComposer());
    expect(document.body.textContent).toContain("确认在本会话中免除后续授权？");
    await act(async () => buttonWithText("我已了解风险，仍要开启").click());
    await act(async () => Promise.resolve());

    expect(setApprovalMode).toHaveBeenCalledWith("session_auto", true);
  });

  it("allows selecting the risky mode before the first message creates a session", async () => {
    await act(async () => {
      useConversationStore.setState({ currentSessionId: null });
    });

    const trigger = document.querySelector<HTMLButtonElement>(
      'button[aria-label="当前授权模式：每次授权，推荐"]',
    );
    expect(trigger?.disabled).toBe(false);

    await act(async () => capturedActions?.requestSessionAutoFromComposer());
    expect(document.body.textContent).toContain("确认在本会话中免除后续授权？");
  });

  it("keeps the risky state visible on the closed composer trigger", async () => {
    await act(async () => {
      useConversationStore.setState({
        approvalMode: "session_auto",
        approvalModeVersion: 4,
      });
    });

    const trigger = document.querySelector<HTMLButtonElement>(
      'button[aria-label="当前授权模式：本会话无需授权，高风险"]',
    );
    expect(trigger?.textContent).toContain("本会话无需授权");
    expect(trigger?.className).toContain("text-warning");
    expect(document.querySelector('[data-slot="dropdown-menu-content"]')).toBeNull();
  });

  it("submits the exact approval-card identity to the atomic endpoint", async () => {
    const enable = vi.spyOn(sessionsApi, "enableSessionAutoForApproval").mockResolvedValue({
      accepted: true,
      session_id: "session-a",
      approval_mode: "session_auto",
      approval_mode_version: 8,
    });
    const applyApprovalModeState = vi.fn();
    const refreshCommands = vi.fn(async () => undefined);
    const refreshFilesystem = vi.fn(async () => undefined);
    useConversationStore.setState({
      approvalModeVersion: 7,
      applyApprovalModeState,
    });
    useCommandRuntimeStore.setState({
      refresh: refreshCommands,
      approvals: {
        "request-a": {
          request_id: "request-a",
          kind: "command",
          session_id: "session-a",
          run_id: "run-a",
          tool_call_id: "tool-a",
          reason: "test",
          requested_lifetime: "once",
          version: 3,
          status: "pending",
          risk: {
            level: "catastrophic",
            fingerprint: "risk-a",
            code: "disk-wipe",
            message: "risk",
            evidence: [],
          },
          details: {
            cmd: "format.com D:",
            argv: ["format.com", "D:"],
            execution: "host_user",
            workdir: "C:/work",
            cwd_source: "explicit",
            mode: "buffered",
            timeout_seconds: 30,
            filesystem_access: "unrestricted",
            additional_roots: [],
            host_derived_roots: [],
            network_access: "unrestricted",
            interactive: false,
          },
        },
      },
    });
    useFilesystemApprovalsStore.setState({ refresh: refreshFilesystem });
    const target: SessionAutoApprovalTarget = {
      kind: "command",
      requestId: "request-a",
      requestVersion: 3,
      sessionId: "session-a",
      runId: "run-a",
      riskCode: "disk-wipe",
    };

    await act(async () => capturedActions?.requestSessionAutoFromApproval(target));
    expect(document.body.textContent).toContain("当前请求风险类别：disk-wipe");
    await act(async () => buttonWithText("我已了解风险，仍要开启").click());

    expect(enable).toHaveBeenCalledWith({
      requestId: "request-a",
      sessionId: "session-a",
      runId: "run-a",
      requestVersion: 3,
      modeVersion: 7,
    });
    expect(applyApprovalModeState).toHaveBeenCalledWith({
      accepted: true,
      session_id: "session-a",
      approval_mode: "session_auto",
      approval_mode_version: 8,
    });
    expect(refreshCommands).toHaveBeenCalledTimes(1);
    expect(refreshFilesystem).toHaveBeenCalledTimes(1);
  });

  it("closes an approval-card confirmation when the exact request becomes stale", async () => {
    const target: SessionAutoApprovalTarget = {
      kind: "command",
      requestId: "request-a",
      requestVersion: 1,
      sessionId: "session-a",
      runId: "run-a",
    };
    useCommandRuntimeStore.setState({
      approvals: {
        "request-a": {
          request_id: "request-a",
          kind: "command",
          session_id: "session-a",
          run_id: "run-a",
          tool_call_id: "tool-a",
          reason: "test",
          requested_lifetime: "once",
          version: 1,
          status: "pending",
          details: {
            cmd: "echo ok",
            argv: ["echo", "ok"],
            execution: "host_user",
            workdir: "C:/work",
            cwd_source: "explicit",
            mode: "buffered",
            timeout_seconds: 30,
            filesystem_access: "unrestricted",
            additional_roots: [],
            host_derived_roots: [],
            network_access: "unrestricted",
            interactive: false,
          },
        },
      },
    });

    await act(async () => capturedActions?.requestSessionAutoFromApproval(target));
    expect(document.body.textContent).toContain("确认在本会话中免除后续授权？");

    await act(async () => {
      useCommandRuntimeStore.setState((state) => ({
        approvals: {
          ...state.approvals,
          "request-a": { ...state.approvals["request-a"], status: "cancelled", version: 2 },
        },
      }));
    });

    expect(document.body.textContent).not.toContain("确认在本会话中免除后续授权？");
  });
});
