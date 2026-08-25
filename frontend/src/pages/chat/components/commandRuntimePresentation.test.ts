import { describe, expect, it } from "vitest";
import type { CommandRuntimeStatus } from "@/services/api/commandRuntime";
import {
  commandExitedLabel,
  commandNeedsSandboxPreparation,
  parseCommandToolResult,
} from "./commandRuntimePresentation";

function status(ready: boolean): CommandRuntimeStatus {
  return {
    available: true,
    platform: "windows",
    sandbox_capability: {
      state: ready ? "ready" : "repair_required",
      supported: true,
      ready,
      last_error: null,
      version: 2,
    },
    windows_sandbox_ready: ready,
    requires_windows_sandbox_setup: !ready,
  };
}

describe("commandNeedsSandboxPreparation", () => {
  it("lets live ready state override an old setup error", () => {
    expect(commandNeedsSandboxPreparation("windows_sandbox_setup_required", status(true))).toBe(false);
  });

  it("uses the tool result only while live runtime state is unavailable", () => {
    expect(commandNeedsSandboxPreparation("windows_sandbox_became_unavailable", null)).toBe(true);
  });

  it("never routes an approved host-user command through sandbox preparation", () => {
    expect(commandNeedsSandboxPreparation("", status(false), "host_user")).toBe(false);
  });
});

describe("commandExitedLabel", () => {
  it("describes the command process terminal state without implying the whole task completed", () => {
    expect(commandExitedLabel(0, false)).toBe("退出码 0");
    expect(commandExitedLabel(7, false)).toBe("退出码 7");
    expect(commandExitedLabel(null, false)).toBe("退出码 未知");
  });

  it("keeps sandbox denial more prominent than an exit code", () => {
    expect(commandExitedLabel(0, true)).toBe("沙箱拒绝");
  });
});

describe("parseCommandToolResult", () => {
  it("restores terminal process facts from the persisted tool result", () => {
    expect(parseCommandToolResult(JSON.stringify({
      status: "exited",
      exit_code: 0,
      sandbox_denied: false,
      duration_seconds: 0.1,
      dropped_bytes: 12,
      stdout: "ready",
      stderr: "",
      pty: "",
    }))).toEqual({
      error: "",
      message: "",
      status: "exited",
      exitCode: 0,
      sandboxDenied: false,
      durationSeconds: 0.1,
      droppedBytes: 12,
      stdout: "ready",
      stderr: "",
      pty: "",
    });
  });

  it("ignores malformed and unknown process state without inventing completion", () => {
    expect(parseCommandToolResult("not-json")).toMatchObject({
      error: "",
      message: "",
      status: undefined,
      stdout: "",
    });
    expect(parseCommandToolResult('{"status":"completed"}').status).toBeUndefined();
  });

  it("preserves the terminal reason used to explain Agent-requested termination", () => {
    expect(parseCommandToolResult(JSON.stringify({
      status: "cancelled",
      terminal_reason: "agent_requested",
    }))).toMatchObject({
      status: "cancelled",
      terminalReason: "agent_requested",
    });
  });
});
