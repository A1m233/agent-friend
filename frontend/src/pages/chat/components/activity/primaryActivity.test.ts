import { describe, expect, it } from "vitest";

import type { SocialAuthRequestSnapshot } from "@/services";
import type { ChatMessage, MessageBlock, ModelResponseProgress } from "@/types/chat";
import type { RunSnapshot } from "@/types/run";
import type { SessionRunPresentation } from "@/stores/runPresentation";
import type { PendingApproval, RunningCommand } from "./approvalCandidates";
import {
  resolvePrimaryActivity,
  socialAuthRequestRefs,
  type PrimaryActivityInput,
} from "./primaryActivity";

const modelStatus: ModelResponseProgress = {
  phase: "waiting",
  afterTool: false,
  attempt: 1,
  maxAttempts: 2,
  activityVersion: 1,
};

function run(): RunSnapshot {
  return {
    run_id: "run-a",
    session_id: "session-a",
    status: "running",
    outcome: null,
    reason_code: "",
    channel: "text",
    origin: "ag_ui",
    control_scope: "desktop_text",
    created_at: "2026-09-05T00:00:00Z",
    started_at: "2026-09-05T00:00:00Z",
    finished_at: null,
    version: 2,
  };
}

function presentation(
  overrides: Partial<SessionRunPresentation> = {},
): SessionRunPresentation {
  return {
    phase: "running",
    run: run(),
    busy: true,
    canStop: true,
    ...overrides,
  };
}

function message(
  blocks: MessageBlock[] = [],
  runStatus: ModelResponseProgress | undefined = undefined,
): ChatMessage {
  return {
    id: "assistant-a",
    role: "assistant",
    runId: "run-a",
    createdAt: "2026-09-05T00:00:00Z",
    blocks,
    status: "streaming",
    runStatus,
  };
}

function approval(): PendingApproval {
  return {
    kind: "command",
    requestId: "approval-a",
    requestVersion: 1,
    sessionId: "session-a",
    runId: "run-a",
    reason: "需要授权",
    command: "echo ok",
    detailLines: [],
    submitting: false,
    needsSandboxSetup: false,
    riskCode: null,
    riskMessage: null,
  };
}

function command(): RunningCommand {
  return {
    processId: "process-a",
    reason: "运行命令",
    command: "echo ok",
    detail: "普通",
  };
}

function socialAuth(status: SocialAuthRequestSnapshot["status"]): SocialAuthRequestSnapshot {
  return {
    requestId: "social-a",
    sessionId: "session-a",
    platform: "bilibili",
    missingCapabilities: [],
    reason: "需要登录",
    originSurface: "desktop_chat",
    status,
    createdAt: "2026-09-05T00:00:00Z",
    expiresAt: "2026-09-05T00:10:00Z",
    continuationRunId: null,
    errorKind: null,
    challengeKind: null,
  };
}

function authBlock(): MessageBlock {
  return {
    kind: "tool",
    toolCallId: "tool-auth",
    name: "request_social_auth",
    args: "{}",
    result: JSON.stringify({ request_id: "social-a" }),
    status: "done",
  };
}

function input(overrides: Partial<PrimaryActivityInput> = {}): PrimaryActivityInput {
  return {
    currentSessionId: "session-a",
    runPresentation: presentation(),
    messages: [message()],
    pendingApprovals: [],
    runningCommands: [],
    socialAuthSnapshots: {},
    ...overrides,
  };
}

describe("resolvePrimaryActivity", () => {
  const kinds = ["approval", "socialAuth", "command", "compaction", "tool", "model", "run"] as const;
  const combinations = kinds.flatMap((first, index) => (
    kinds.slice(index).map((second) => ({ first, second }))
  ));
  it.each(combinations)("selects only $first when $first and $second coexist", ({ first, second }) => {
    const selected = new Set<string>([first, second]);
    const blocks: MessageBlock[] = [];
    if (selected.has("socialAuth")) blocks.push(authBlock());
    if (selected.has("compaction")) blocks.push({ kind: "compaction", activityId: "compact", status: "running" });
    if (selected.has("tool")) blocks.push({ kind: "tool", toolCallId: "tool", name: "web_search", args: "{}", status: "running" });
    const result = resolvePrimaryActivity(input({
      messages: [message(blocks, selected.has("model") ? modelStatus : undefined)],
      pendingApprovals: selected.has("approval") ? [approval()] : [],
      runningCommands: selected.has("command") ? [command()] : [],
      socialAuthSnapshots: selected.has("socialAuth") ? { "social-a": socialAuth("continuing") } : {},
    }));
    expect(result.kind).toBe(first);
  });

  it.each(["queued", "stopping"] as const)("preserves %s over a stale model status", (phase) => {
    expect(resolvePrimaryActivity(input({
      messages: [message([], modelStatus)],
      runPresentation: presentation({ phase }),
    }))).toMatchObject({ kind: "run", phase });
  });

  it("keeps a pending social-auth tool visible before its request snapshot exists", () => {
    expect(resolvePrimaryActivity(input({
      messages: [message([{ ...authBlock(), status: "running", result: undefined } as MessageBlock], modelStatus)],
    }))).toMatchObject({ kind: "tool", toolCallId: "tool-auth" });
  });

  it("does not treat an unread historical login request as currently active", () => {
    expect(resolvePrimaryActivity(input({ messages: [message([authBlock()], modelStatus)] })))
      .toMatchObject({ kind: "model" });
  });

  it("uses a message generic only when no precise activity exists", () => {
    expect(resolvePrimaryActivity(input())).toEqual({
      surface: "message",
      kind: "run",
      messageId: "assistant-a",
      phase: "running",
    });
  });

  it("uses the dock for a run without a conversation message", () => {
    expect(resolvePrimaryActivity(input({ messages: [] }))).toEqual({
      surface: "dock",
      kind: "run",
      phase: "running",
      label: "任务仍在继续…",
    });
  });

  it.each([
    {
      name: "model over generic",
      value: input({ messages: [message([], modelStatus)] }),
      expected: ["message", "model"],
    },
    {
      name: "tool over model",
      value: input({
        messages: [message([{
          kind: "tool",
          toolCallId: "tool-a",
          name: "web_search",
          args: "{}",
          status: "running",
        }], modelStatus)],
      }),
      expected: ["message", "tool"],
    },
    {
      name: "compaction over tool and model",
      value: input({
        messages: [message([
          {
            kind: "tool",
            toolCallId: "tool-a",
            name: "web_search",
            args: "{}",
            status: "running",
          },
          { kind: "compaction", activityId: "compact-a", status: "running" },
        ], modelStatus)],
      }),
      expected: ["message", "compaction"],
    },
    {
      name: "command over message activities",
      value: input({
        messages: [message([
          { kind: "compaction", activityId: "compact-a", status: "running" },
        ], modelStatus)],
        runningCommands: [command()],
      }),
      expected: ["dock", "command"],
    },
    {
      name: "social auth over command",
      value: input({
        messages: [message([authBlock()])],
        runningCommands: [command()],
        socialAuthSnapshots: { "social-a": socialAuth("pending") },
      }),
      expected: ["message", "socialAuth"],
    },
    {
      name: "approval over every other candidate",
      value: input({
        messages: [message([
          authBlock(),
          { kind: "compaction", activityId: "compact-a", status: "running" },
        ], modelStatus)],
        pendingApprovals: [approval()],
        runningCommands: [command()],
        socialAuthSnapshots: { "social-a": socialAuth("pending") },
      }),
      expected: ["dock", "approval"],
    },
  ])("selects $name", ({ value, expected }) => {
    const result = resolvePrimaryActivity(value);
    expect([result.surface, result.kind]).toEqual(expected);
  });

  it("does not let a completed social auth request mask the active run", () => {
    const result = resolvePrimaryActivity(input({
      messages: [message([authBlock()])],
      socialAuthSnapshots: { "social-a": socialAuth("completed") },
    }));

    expect(result).toMatchObject({ surface: "message", kind: "run" });
  });

  it("returns none only when the session has no current activity", () => {
    expect(resolvePrimaryActivity(input({
      runPresentation: presentation({ phase: "idle", run: null, busy: false, canStop: false }),
      messages: [],
    }))).toEqual({ surface: "none", kind: "none" });
  });

  it("keeps recovery and cancellation labels in the single selected result", () => {
    expect(resolvePrimaryActivity(input({
      runPresentation: presentation({ phase: "recovering", run: null, canStop: false }),
      messages: [],
    }))).toMatchObject({
      surface: "dock",
      kind: "run",
      label: "正在恢复这段对话的运行状态…",
    });
    expect(resolvePrimaryActivity(input({
      runPresentation: presentation({ phase: "stopping", canStop: false }),
    }))).toMatchObject({
      surface: "message",
      kind: "run",
      label: "正在取消当前任务，任务尚未结束…",
    });
  });
});

describe("socialAuthRequestRefs", () => {
  it("extracts only valid request ids with stable message and tool identity", () => {
    expect(socialAuthRequestRefs([
      message([
        authBlock(),
        {
          kind: "tool",
          toolCallId: "invalid",
          name: "request_social_auth",
          args: "{}",
          result: "not-json",
          status: "done",
        },
      ]),
    ])).toEqual([{
      messageId: "assistant-a",
      toolCallId: "tool-auth",
      requestId: "social-a",
    }]);
  });
});
