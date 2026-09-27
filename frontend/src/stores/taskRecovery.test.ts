import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import type { ChatMessage } from "@/types/chat";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { continuationRecoveryMode, groupRecoveryMessages, parseRunErrorRecovery } from "./taskRecovery";

const message = (runId: string, sourceRunId?: string): ChatMessage => ({
  id: runId, runId, sourceRunId, role: "assistant", status: "error", error: `error-${runId}`,
  createdAt: "2026-09-16T00:00:00Z", runStartedAt: "2026-09-16T00:00:00Z",
  runFinishedAt: "2026-09-16T00:00:02Z", blocks: [{ kind: "text", mid: runId, text: runId }],
});

describe("task recovery projection", () => {
  it("selects explicit compaction and legacy compaction without reading error prose", () => {
    const eligible = { ...message("failed"), canContinue: true };
    expect(continuationRecoveryMode({ ...eligible, recoveryAction: "compact_then_resume" }))
      .toBe("compact_then_resume");
    expect(continuationRecoveryMode({ ...eligible, errorCode: "context_compaction_unavailable" }))
      .toBe("compact_then_resume");
    expect(continuationRecoveryMode({ ...eligible, errorCode: "context_compaction_unavailable", recoveryAction: "retry" }))
      .toBe("automatic");
    expect(continuationRecoveryMode({ ...eligible, error: "当前任务必须保留的内容已超过上下文预算" }))
      .toBe("automatic");
    expect(continuationRecoveryMode({ ...eligible, canContinue: false, recoveryAction: "compact_then_resume" }))
      .toBe("automatic");
  });

  it.each(["compact_then_resume", "retry", "adjust_input", "adjust_configuration", "wait"])(
    "normalizes the %s action from either protocol projection", (action) => {
      expect(parseRunErrorRecovery({ reason: "reason", recoveryAction: action }, "camel"))
        .toEqual(parseRunErrorRecovery({ reason: "reason", recovery_action: action }, "snake"));
      expect(parseRunErrorRecovery({ recovery_action: action }, "snake").recoveryAction).toBe(action);
    },
  );

  it("uses one current error across multiple linked attempts without losing completed work", () => {
    const grouped = groupRecoveryMessages([message("a"), message("b", "a"), message("c", "b")]);
    expect(grouped).toHaveLength(1);
    expect(grouped[0].error).toBe("error-c");
    expect(grouped[0].blocks).toHaveLength(3);
    expect(grouped[0].priorExecutionSeconds).toBe(4);
    expect(grouped[0].runId).toBe("c");
  });

  it("does not merge independent errors or cross a new user task", () => {
    expect(groupRecoveryMessages([message("a"), message("b")])).toHaveLength(2);
    const user: ChatMessage = { ...message("user"), role: "user" };
    expect(groupRecoveryMessages([message("a"), user, message("b", "a")])).toHaveLength(3);
  });

  it("replaces the unfinished draft on manual continuation and excludes manual waiting", () => {
    const old = message("a");
    old.blocks.push({ kind: "text", mid: "draft", text: "unfinished", partial: true });
    const next = { ...message("b", "a"), status: "complete" as const, error: undefined,
      runStartedAt: "2026-09-16T01:00:00Z", runFinishedAt: "2026-09-16T01:00:03Z" };
    const [grouped] = groupRecoveryMessages([old, next]);
    expect(grouped.blocks).toEqual([...message("a").blocks, ...next.blocks]);
    expect(grouped.priorExecutionSeconds).toBe(2);
    expect(grouped.error).toBeUndefined();
    expect(groupRecoveryMessages([{ ...old, runFinishedAt: undefined }, next])[0].priorExecutionSeconds).toBeNull();
  });

  it("retracts only the failed response items equally over SSE and push", () => {
    const initial = { ...message("saved"), blocks: [...message("saved").blocks, ...message("partial").blocks] };
    const push = applyPushConversationEvent(initial, { type: "assistant_message_reset", message_ids: ["partial"] }, "r");
    const sse = applyAguiEvent(initial, {
      type: EventType.CUSTOM, name: "agent_friend.assistant_message_reset", value: { messageIds: ["partial"] },
    } as BaseEvent);
    expect(push.blocks).toEqual(message("saved").blocks);
    expect(sse).toEqual(push);
    expect(applyPushConversationEvent(push, { type: "assistant_message_reset", message_ids: ["partial"] }, "r")).toEqual(push);
  });

  it("reloads durable recovery linkage and chooses the latest terminal", () => {
    const events = ["a", "b"].flatMap((run, index) => [
      { type: "run_started", uuid: `${run}-start`, ts: "2026-09-16T00:00:00Z", meta: { run_id: run },
        payload: { started_at: "2026-09-16T00:00:00Z", ...(index ? { source_run_id: "a", root_run_id: "a" } : {}) } },
      { type: "run_error", uuid: `${run}-error`, ts: "2026-09-16T00:00:01Z", meta: { run_id: run }, payload: { message: `error-${run}` } },
      { type: "run_terminal", uuid: `${run}-end`, ts: "2026-09-16T00:00:02Z", meta: { run_id: run }, payload: { status: "failed", finished_at: "2026-09-16T00:00:02Z" } },
    ]);
    const grouped = groupRecoveryMessages(projectSessionEvents(events));
    expect(grouped).toHaveLength(1);
    expect(grouped[0].error).toBe("error-b");
  });

  it("keeps an empty stopped recovery attached after reload without resurrecting the old failure", () => {
    const events = [
      { type: "run_error", uuid: "error-a", ts: "2026-09-16T00:00:00Z",
        meta: { run_id: "a" }, payload: { message: "old error" } },
      { type: "run_started", uuid: "start-b", ts: "2026-09-16T00:00:01Z",
        meta: { run_id: "b" }, payload: { source_run_id: "a", root_run_id: "a" } },
      { type: "run_terminal", uuid: "end-b", ts: "2026-09-16T00:00:02Z",
        meta: { run_id: "b" }, payload: { status: "interrupted" } },
    ];
    const grouped = groupRecoveryMessages(projectSessionEvents(events));
    expect(grouped).toHaveLength(1);
    expect(grouped[0].runId).toBe("b");
    expect(grouped[0].status).toBe("interrupted");
    expect(grouped[0].error).toBeUndefined();
  });
});
