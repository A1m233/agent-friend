import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import type { ChatMessage } from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { canDiscloseExecution, normalizeAssistantPhase, shouldCollapseExecution } from "./assistantPhase";
import { executionSections } from "@/pages/chat/components/executionDisclosurePolicy";
import { buildMessagePresentationSegments } from "@/pages/chat/components/toolActivityPresentation";

const empty = (): ChatMessage => ({ id: "m", role: "assistant", runId: "r", createdAt: "t", blocks: [], status: "streaming" });
const ev = (type: string, uuid: string, payload: Record<string, unknown>, run = "r"): SessionEvent => ({
  type, uuid, payload, meta: { run_id: run }, ts: "2026-09-05T00:00:00Z",
});

describe("per-run assistant phases", () => {
  it("updates completed metadata in place without moving text across tools or reviving the run", () => {
    const message: ChatMessage = { ...empty(), status: "complete", blocks: [
      { kind: "text", mid: "a", text: "before", phase: "final_answer", itemIndex: 0 },
      { kind: "tool", toolCallId: "tool", name: "search", args: "{}", status: "done" },
      { kind: "text", mid: "b", text: "after", phase: "final_answer", itemIndex: 1 },
    ] };
    const pull = applyAguiEvent(message, { type: EventType.CUSTOM,
      name: "agent_friend.assistant_message_phase", value: { messageId: "a", phase: "commentary" } } as BaseEvent);
    const push = applyPushConversationEvent(message, { type: "assistant_message_phase", message_id: "a", phase: "commentary" }, "r");
    expect(push).toEqual(pull);
    expect(push.blocks).toEqual([{ ...message.blocks[0], phase: "commentary" }, ...message.blocks.slice(1)]);
    expect(push.status).toBe("complete");
    expect(applyPushConversationEvent(message, { type: "assistant_message_phase", message_id: "missing", phase: "commentary" }, "r").blocks).toEqual(message.blocks);
  });

  it("loads run timing from product facts, never from reconciliation write time", () => {
    const messages = projectSessionEvents([
      ev("run_started", "start", { started_at: "2026-09-05T00:00:00Z" }),
      ev("user_message", "u", { content: "hi" }),
      ev("assistant_message", "a", { content: "hello" }),
      ev("run_terminal", "end", { status: "completed", started_at: "2026-09-05T00:00:00Z", finished_at: "2026-09-05T00:00:03Z" }),
    ]);
    expect(messages[1].runStartedAt).toBe("2026-09-05T00:00:00Z");
    expect(messages[1].runFinishedAt).toBe("2026-09-05T00:00:03Z");
    const interrupted = projectSessionEvents([
      ev("user_message", "u", { content: "hi" }),
      ev("assistant_message", "a", { content: "partial" }),
      ev("run_terminal", "end", { status: "interrupted", recovered: true }),
    ])[1];
    expect(interrupted.runFinishedAt).toBeUndefined();
  });

  it("keeps a header for a started user run without text, not orphan metadata", () => {
    const result = projectSessionEvents([
      ev("run_started", "start", { started_at: "2026-09-05T00:00:00Z" }),
      ev("user_message", "u", { content: "hi" }),
    ]);
    expect(result).toHaveLength(2);
    expect(result[1]).toMatchObject({ role: "assistant", runId: "r", blocks: [], status: "interrupted", runStartedAt: "2026-09-05T00:00:00Z" });
    expect(result[1].runFinishedAt).toBeUndefined();
    expect(projectSessionEvents([ev("run_started", "orphan", { started_at: "2026-09-05T00:00:00Z" })])).toEqual([]);
  });
  it("uses durable run facts across model changes and never rollout/current catalog", () => {
    const events = [
      ev("user_message", "u", { content: "hi" }),
      ev("assistant_phase", "p", { mode: "native", validation: "valid" }),
      ev("assistant_message", "c", { content: "working", phase: "commentary" }),
      ev("assistant_message", "f", { content: "done", phase: "final_answer" }),
      ev("run_terminal", "rt", { status: "completed" }),
      ev("model_change", "mc", { model: "legacy" }),
      ev("user_message", "u2", { content: "hi" }, "legacy"),
      ev("assistant_phase", "p2", { mode: "legacy", validation: "missing_final" }, "legacy"),
      ev("assistant_message", "f2", { content: "ordinary" }, "legacy"),
      ev("model_transcript_item", "fake", { role: "assistant", phase: "final_answer", content: "not UI" }),
    ];
    const messages = projectSessionEvents(events).filter((message) => message.role === "assistant");
    expect(messages).toHaveLength(2);
    expect(shouldCollapseExecution(messages[0])).toBe(true);
    expect(canDiscloseExecution(messages[1])).toBe(false);
    expect(JSON.stringify(messages)).not.toContain("not UI");
    expect(shouldCollapseExecution(projectSessionEvents(events)[1])).toBe(true);
  });

  it("pull and push retain the same IDs and source ordering for interleaved items", () => {
    let push = empty();
    let pull = empty();
    for (const [mid, index, text, phase] of [
      ["final", 2, "end", "final_answer"], ["first", 0, "a", "commentary"],
      ["second", 1, "b", "commentary"], ["first", 0, "c", "commentary"],
    ] as const) {
      push = applyPushConversationEvent(push, { type: "text_delta", message_id: mid,
        item_index: index, phase, text }, "r");
      const events = [
        { type: EventType.TEXT_MESSAGE_START, messageId: mid, role: "assistant" },
        { type: EventType.CUSTOM, name: "agent_friend.assistant_message", value: { messageId: mid, phase, itemIndex: index } },
        { type: EventType.TEXT_MESSAGE_CONTENT, messageId: mid, delta: text },
      ];
      for (const event of events) pull = applyAguiEvent(pull, event as BaseEvent);
    }
    expect(push.blocks).toEqual(pull.blocks);
    expect(push.blocks.map((block) => block.kind === "text" && [block.mid, block.text])).toEqual([
      ["first", "ac"], ["second", "b"], ["final", "end"],
    ]);
  });

  it("keeps unknown, errors and interaction cards outside contiguous execution sections", () => {
    const message: ChatMessage = { ...empty(), assistantPhase: { mode: "native", validation: "valid" },
      status: "complete", runOutcome: "completed", blocks: [
        { kind: "text", mid: "a", text: "progress", phase: "commentary" },
        { kind: "text", mid: "b", text: "keep", phase: "unknown" },
        { kind: "tool", toolCallId: "auth", name: "request_social_auth", args: "{}", status: "done" },
        { kind: "tool", toolCallId: "error", name: "web_search", args: "{}", status: "error" },
        { kind: "text", mid: "f", text: "answer", phase: "final_answer" },
      ] };
    expect(executionSections(message, buildMessagePresentationSegments(message.blocks)).map((s) => s.execution))
      .toEqual([true, false, false, false, false]);
    for (const status of ["streaming", "interrupted", "error"] as const) {
      expect(shouldCollapseExecution({ ...message, status })).toBe(false);
    }
    for (const validation of ["missing_final", "conflict", "pending"] as const) {
      expect(shouldCollapseExecution({ ...message, assistantPhase: { mode: "native", validation } })).toBe(false);
    }
    expect(normalizeAssistantPhase("final")).toBe("unknown");
  });

  it("hides every ambient run item without hiding the next conversation", () => {
    const result = projectSessionEvents([
      ev("system_trigger", "trigger", { output_visibility: "user", presentation: "ambient" }),
      ev("assistant_message", "c", { content: "ambient commentary", phase: "commentary" }),
      ev("assistant_message", "f", { content: "ambient final", phase: "final_answer" }),
      ev("user_message", "u", { content: "hi" }, "next"),
      ev("assistant_message", "answer", { content: "shown" }, "next"),
    ]);
    expect(result).toHaveLength(2);
    expect(JSON.stringify(result)).not.toContain("ambient");
  });
});
