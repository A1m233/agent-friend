import { expect, it } from "vitest";
import type { HistoryEvent, HistoryPage } from "@/types/history";
import { createHistoryProjection } from "./historyProjection";
import { applyHistoryRunEvents } from "./historyLiveEvents";

const item = (uuid: string, type: string, payload: Record<string, unknown>): HistoryEvent => ({ uuid, type,
  payload, ts: "2026-10-08", body: { state: "inline" }, message_id: "run-message", meta: { run_id: "r" },
  run: { run_id: "r" } });
const page = (events: HistoryEvent[]): HistoryPage => ({ session_id: "s", title: "s", model: "m", persona: "p",
  content_version: "v", history_epoch: "e", history_items: events, before_cursor: null, after_cursor: null,
  has_before: false, has_after: false });

it("routes a late tool result to its fragment instead of the run's final text", () => {
  const request = item("tool", "tool_call_request", { event_tool_call_id: "tc", tool_name: "test", args: {} });
  const text = item("text", "assistant_message", { content: "later" });
  const messages = createHistoryProjection().project(page([request, text]));
  const next = applyHistoryRunEvents(messages, "r", [{ type: "tool_call_result", event_tool_call_id: "tc", text: "result", is_error: false }])!;
  expect(next[0].blocks[0]).toMatchObject({ kind: "tool", result: "result", status: "done" });
  expect(next[1]).toBe(messages[1]);
});

it("keeps unknown live text in its own reducer-owned tail and avoids duplicated starts", () => {
  const request = item("tool", "tool_call_request", { event_tool_call_id: "tc", tool_name: "test", args: {} });
  const messages = createHistoryProjection().project(page([request]));
  const next = applyHistoryRunEvents(messages, "r", [
    { type: "tool_call_request", event_tool_call_id: "tc", tool_name: "test", args: {} },
    { type: "text_delta", message_id: "new", text: "new text" },
  ])!;
  expect(next).toHaveLength(2);
  expect(next[0]).toBe(messages[0]);
  expect(next[1].history).toBeUndefined();
  expect(next[1].blocks[0]).toMatchObject({ mid: "new", text: "new text" });
});

it("retains a deferred request when its real result arrives and never fabricates arguments", () => {
  const request = { ...item("tool", "tool_call_request", { event_tool_call_id: "tc", tool_name: "test" }),
    body: { state: "deferred" as const, ref: "tool", byte_length: 99999 } };
  const messages = createHistoryProjection().project(page([request]));
  const next = applyHistoryRunEvents(messages, "r", [{ type: "tool_call_result", event_tool_call_id: "tc", text: "done" }])!;
  expect(next[0].blocks).toEqual([]);
  expect(next[0].history?.event.body.state).toBe("deferred");
  expect(next[0].history?.event.result_event?.payload?.content).toBe("done");
});
