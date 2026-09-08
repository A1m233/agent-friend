import { EventType, type BaseEvent } from "@ag-ui/core";
import { describe, expect, it } from "vitest";
import type { ChatMessage, MessageBlock, TextBlock, ToolBlock } from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { applyAguiEvent, applyPushConversationEvent } from "@/stores/conversationReducer";
import { projectSessionEvents } from "@/stores/sessionProjection";
import { executionDisclosurePolicy, executionSections, socialAuthNeedsUserAction } from "./executionDisclosurePolicy";
import { buildMessagePresentationSegments } from "./toolActivityPresentation";

const text = (mid: string, value: string, phase?: TextBlock["phase"]): TextBlock => ({
  kind: "text", mid, text: value, ...(phase === undefined ? {} : { phase }),
});
const tool = (toolCallId: string, overrides: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool", toolCallId, name: "calculate", args: "{}", status: "done", ...overrides,
});
const completed = (blocks: MessageBlock[], overrides: Partial<ChatMessage> = {}): ChatMessage => ({
  id: "assistant", role: "assistant", runId: "run", createdAt: "2026-09-06T00:00:00Z",
  assistantPhase: { mode: "legacy", validation: "missing_final" },
  status: "complete", runOutcome: "completed", blocks, ...overrides,
});
const event = (type: string, uuid: string, payload: Record<string, unknown>): SessionEvent => ({
  type, uuid, payload, ts: "2026-09-06T00:00:00Z", meta: { run_id: "run" },
});

function partition(message: ChatMessage) {
  const segments = buildMessagePresentationSegments(message.blocks);
  const sections = executionSections(message, segments);
  const processIndexes = new Set(sections.filter((section) => section.execution).flatMap((section) => section.indexes));
  return {
    sections,
    process: segments.filter((_, index) => processIndexes.has(index)),
    visible: segments.filter((_, index) => !processIndexes.has(index)),
  };
}

describe("execution disclosure without native assistant phases", () => {
  it("derives the final logical item without modifying source phases, text or identities", () => {
    const message = completed([text("before", "Checking"), tool("lookup"), text("answer", "The answer", "unknown")]);
    const original = structuredClone(message);
    expect(executionDisclosurePolicy(message)).toEqual({ source: "inferred", finalTextId: "answer", collapse: true });
    const { process, visible } = partition(message);
    expect(process.map((segment) => segment.kind)).toEqual(["block", "toolActivity"]);
    expect(visible).toMatchObject([{ kind: "block", block: { mid: "answer", text: "The answer", phase: "unknown" } }]);
    expect(message).toEqual(original);
    expect(message.blocks[0]).not.toHaveProperty("phase");
  });

  it("does not create an execution section for a direct answer", () => {
    const message = completed([text("answer", "A direct answer")]);
    expect(executionDisclosurePolicy(message).source).toBe("inferred");
    expect(partition(message).sections.some((section) => section.execution)).toBe(false);
  });

  it("orders unique items by first appearance, keeping every fragment of the final item visible", () => {
    const message = completed([
      text("before", "Progress one"), tool("lookup"), text("answer", "Answer one"),
      text("before", "Late progress fragment"), text("answer", "Answer two"),
    ]);
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "inferred", finalTextId: "answer" });
    const { process, visible } = partition(message);
    expect(visible.filter((segment) => segment.kind === "block").map((segment) => segment.block))
      .toEqual([message.blocks[2], message.blocks[4]]);
    expect(process.filter((segment) => segment.kind === "block").map((segment) => segment.block))
      .toEqual([message.blocks[0], message.blocks[3]]);
  });

  it("does not promote a late fragment of an earlier item to the final item", () => {
    const message = completed([text("before", "First"), text("answer", "Answer"), text("before", "Late delta")]);
    expect(executionDisclosurePolicy(message).finalTextId).toBe("answer");
  });

  it("skips a trailing whitespace-only item", () => {
    const message = completed([text("before", "Progress"), text("answer", "Answer"), text("empty", " \n\t")]);
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "inferred", finalTextId: "answer" });
  });

  it.each([
    [text("before", "I will run a tool"), tool("lookup")],
    [text("answer", "Before"), tool("lookup"), text("answer", "After with the same identity")],
    [text("answer", "Before"), tool("lookup"), text("empty", " ")],
  ])("does not treat a pre-tool item as the final answer (%#)", (...blocks) => {
    expect(executionDisclosurePolicy(completed(blocks))).toMatchObject({ source: "none", collapse: false });
  });

  it.each([
    { status: "streaming" },
    { status: "interrupted", runOutcome: "cancelled" },
    { status: "error", runOutcome: "failed" },
    { runOutcome: "skipped" },
    { runOutcome: "expired" },
    { runOutcome: "rejected" },
    { runOutcome: undefined },
    { runId: undefined },
    { error: "An error remains visible" },
  ] satisfies Partial<ChatMessage>[])("requires trustworthy success and no error (%j)", (overrides) => {
    const message = completed([text("before", "Progress"), text("answer", "Answer")], overrides);
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "none", collapse: false });
    expect(partition(message).sections.every((section) => !section.execution)).toBe(true);
  });

  it("does not hide a running tool even if a stale message already claims success", () => {
    const message = completed([text("before", "Progress"), tool("lookup", { status: "running" }), text("answer", "Answer")]);
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "none", collapse: false });
  });

  it("preserves a legacy item-boundary conflict even when the whole run completed", () => {
    const message = completed([text("before", "Progress"), text("answer", "Answer")], {
      assistantPhase: { mode: "legacy", validation: "conflict" },
    });
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "none", collapse: false });
    expect(partition(message).sections.every((section) => !section.execution)).toBe(true);
  });

  it.each([{ blocks: [] }, { blocks: [text("empty", " \n")] }, { blocks: [tool("lookup")] }])("does not invent an answer when no nonempty text exists (%#)", ({ blocks }) => {
    expect(executionDisclosurePolicy(completed(blocks))).toMatchObject({ source: "none", collapse: false });
  });

  it.each(["commentary", "final_answer"] as const)("does not mix inferred phases with legacy native metadata (%s)", (phase) => {
    const message = completed([text("before", "Known native value", phase), text("answer", "Unknown answer")]);
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "none", collapse: false });
    expect(message.blocks[0]).toMatchObject({ phase });
  });

  it("folds finished request tools, warnings and errors while preserving fixed facts", () => {
    const protectedBlocks: MessageBlock[] = [
      tool("auth", { name: "request_social_auth" }),
      tool("access", { name: "request_filesystem_access", result: '{"status":"granted"}' }),
      tool("warning", { name: "exec_command", result: '{"error":"approval_denied"}' }),
      tool("error", { status: "error" }),
      { kind: "compaction", activityId: "compact", status: "complete" },
      { kind: "reminder", occurrenceId: "reminder", todoId: "todo", reminderId: "r", title: "Reminder",
        description: "Keep this visible", scheduledAtUtc: "t", scheduledLocal: "t", timezone: "UTC", recurrence: "none", todoStatus: "pending" },
    ];
    const blocks = protectedBlocks.flatMap((block, index) => [block, text(`progress-${index}`, `Progress ${index}`)]);
    const message = completed([...blocks, text("answer", "Answer")]);
    expect(executionDisclosurePolicy(message).source).toBe("inferred");
    const { process, visible } = partition(message);
    expect(process.filter((segment) => segment.kind !== "block")).toHaveLength(4);
    expect(visible).toHaveLength(3);
    expect(visible.at(-1)).toMatchObject({ kind: "block", block: { mid: "answer" } });
  });
});

describe("only current user actions remain outside execution", () => {
  it.each(["native", "legacy"] as const)("folds an entire mixed tool group in %s runs", (mode) => {
    const message = completed([
      tool("failed", { name: "exec_command", status: "error" }),
      tool("authorized", { name: "request_filesystem_access", result: '{"status":"granted"}' }),
      tool("retry", { name: "exec_command", result: '{"status":"exited","exit_code":0}' }),
      text("answer", "Recovered and finished", mode === "native" ? "final_answer" : undefined),
    ], { assistantPhase: { mode, validation: mode === "native" ? "valid" : "missing_final" } });
    expect(executionDisclosurePolicy(message).collapse).toBe(true);
    expect(partition(message).visible).toMatchObject([{ kind: "block", block: { mid: "answer" } }]);
    expect(partition(message).process).toMatchObject([{ kind: "toolActivity", blocks: [{ toolCallId: "failed" }, { toolCallId: "authorized" }, { toolCallId: "retry" }] }]);
  });

  it.each([
    "pending", "login_in_progress", "authenticated", "continuing", "completed",
    "declined", "cancelled", "expired", "login_failed", "continuation_failed", undefined,
  ] as const)("uses live social authorization status %s without exposing adjacent tools", (status) => {
    const message = completed([tool("before"), tool("auth", { name: "request_social_auth" }), tool("after"), text("answer", "Answer")]);
    const actionable = socialAuthNeedsUserAction(status);
    expect(actionable).toBe(status === "pending" || status === "login_in_progress");
    const segments = buildMessagePresentationSegments(message.blocks);
    const sections = executionSections(message, segments, executionDisclosurePolicy(message), actionable ? ["auth"] : []);
    const visible = sections.filter((section) => !section.execution).flatMap((section) => section.indexes);
    expect(visible).toEqual(actionable ? [1, 3] : [3]);
  });
});

describe("native disclosure remains authoritative", () => {
  it("retains native final and unknown visibility instead of inferring the last text", () => {
    const message = completed([
      text("before", "Progress", "commentary"), text("unknown", "Unclassified", "unknown"), text("answer", "Answer", "final_answer"),
    ], { assistantPhase: { mode: "native", validation: "valid" } });
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "native", collapse: true });
    expect(partition(message).visible.map((segment) => segment.kind === "block" && segment.block))
      .toEqual([message.blocks[1], message.blocks[2]]);
  });

  it.each(["missing_final", "pending"] as const)("does not infer a final when native validation is %s", (validation) => {
    const message = completed([text("before", "Progress", "commentary"), text("last", "Unknown")], {
      assistantPhase: { mode: "native", validation },
    });
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "native", collapse: false });
    expect(partition(message).visible).toMatchObject([{ kind: "block", block: { mid: "last" } }]);
  });

  it("does not convert a native conflict into inferred success", () => {
    const message = completed([text("before", "Answer", "final_answer"), tool("lookup"), text("last", "More text")], {
      assistantPhase: { mode: "native", validation: "conflict" },
    });
    expect(executionDisclosurePolicy(message)).toMatchObject({ source: "none", collapse: false });
    expect(partition(message).sections.every((section) => !section.execution)).toBe(true);
  });
});

describe("history and stream projection use the same fallback evidence", () => {
  const history = (terminal?: Record<string, unknown>) => projectSessionEvents([
    event("user_message", "user", { content: "Question" }),
    event("assistant_phase", "phase", { mode: "legacy", validation: "missing_final" }),
    event("assistant_message", "before", { content: "Progress" }),
    event("assistant_message", "answer", { content: "Answer" }),
    ...(terminal ? [event("run_terminal", "terminal", terminal)] : []),
  ]).find((message) => message.role === "assistant")!;

  it("does not infer from the default complete status of old history", () => {
    const old = history();
    expect(old.status).toBe("complete");
    expect(old.runOutcome).toBeUndefined();
    expect(executionDisclosurePolicy(old)).toMatchObject({ source: "none", collapse: false });
    expect(executionDisclosurePolicy(history({ status: "completed" })))
      .toEqual({ source: "inferred", finalTextId: "answer", collapse: true });
    expect(executionDisclosurePolicy(history({ status: "interrupted", recovered: true })))
      .toMatchObject({ source: "none", collapse: false });
  });

  it("allows old history with trusted run completion without backfilling a phase fact", () => {
    const message = projectSessionEvents([
      event("user_message", "user", { content: "Question" }),
      event("assistant_message", "before", { content: "Progress" }),
      event("assistant_message", "answer", { content: "Answer" }),
      event("run_terminal", "terminal", { status: "completed" }),
    ])[1];
    expect(message.assistantPhase).toBeUndefined();
    expect(executionDisclosurePolicy(message)).toEqual({ source: "inferred", finalTextId: "answer", collapse: true });
    expect(message.assistantPhase).toBeUndefined();
  });

  it("matches AG-UI and durable completion while push done alone does not prove run success", () => {
    let pull = completed([], { status: "streaming", runOutcome: undefined });
    let push = structuredClone(pull);
    for (const [mid, value] of [["before", "Progress"], ["answer", "Answer"]]) {
      pull = applyAguiEvent(pull, { type: EventType.TEXT_MESSAGE_START, messageId: mid, role: "assistant" } as BaseEvent);
      pull = applyAguiEvent(pull, { type: EventType.TEXT_MESSAGE_CONTENT, messageId: mid, delta: value } as BaseEvent);
      push = applyPushConversationEvent(push, { type: "text_delta", message_id: mid, text: value }, "run");
    }
    expect(executionDisclosurePolicy(pull).source).toBe("none");
    expect(executionDisclosurePolicy(push).source).toBe("none");
    push = applyPushConversationEvent(push, { type: "done", status: "completed" }, "run");
    expect(push.status).toBe("complete");
    expect(executionDisclosurePolicy(push).source).toBe("none");
    pull = applyAguiEvent(pull, { type: EventType.RUN_FINISHED, threadId: "session", runId: "run", result: { status: "completed" } } as BaseEvent);
    expect(executionDisclosurePolicy(pull)).toEqual(executionDisclosurePolicy(history({ status: "completed" })));
    expect(partition(pull).sections.map((section) => ({ execution: section.execution, textIds: section.textIds })))
      .toEqual(partition(history({ status: "completed" })).sections.map((section) => ({ execution: section.execution, textIds: section.textIds })));
  });
});
