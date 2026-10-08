import { describe, expect, it } from "vitest";
import type { HistoryEvent, HistoryPage } from "@/types/history";
import { createHistoryProjection, projectHistoryEvent } from "./historyProjection";
import { createSessionHistoryPages } from "./sessionHistoryPages";

const event = (uuid: string): HistoryEvent => ({ type: "assistant_message", uuid,
  ts: "2026-10-08T00:00:00Z", payload: { content: uuid }, meta: { run_id: "run" },
  message_id: "logical", body: { state: "inline" }, message_start: false, message_end: true,
  run: { run_id: "run", status: "completed" } });
const page = (items: HistoryEvent[], version = "1"): HistoryPage => ({ session_id: "s", title: "s",
  persona: "p", model: "m", history_epoch: "epoch", content_version: version,
  history_items: items, before_cursor: items[0]?.uuid ?? null, after_cursor: items.at(-1)?.uuid ?? null,
  has_before: true, has_after: false });

describe("bounded history projection", () => {
  it("preserves a hydrated full error instead of replacing it with the run preview", () => {
    const failure: HistoryEvent = { ...event("failure"), type: "run_error",
      payload: { message: "full error " + "detail ".repeat(2000), code: "runtime_error" },
      run: { run_id: "run", status: "failed", error: "full error preview", error_truncated: true,
        error_event_uuid: "failure", error_code: "runtime_error" } };
    const message = projectHistoryEvent(page([failure]), failure)!;
    expect(message.error).toBe(failure.payload?.message);
    expect(message.error!.length).toBeGreaterThan(10000);
  });
  it("keeps logical identity while projecting each long run block as a stable virtual row", () => {
    const projection = createHistoryProjection(); const first = event("a"); const last = event("b");
    const initial = projection.project(page([first, last]));
    const all = projection.project(page([{ ...event("older"), message_id: "other" }, first, last]));
    expect(all.map((message) => message.id)).toEqual(["older", "a", "b"]);
    expect(all.map((message) => message.history?.messageId)).toEqual(["other", "logical", "logical"]);
    expect(all[2]).toBe(initial[1]);
    expect(all[2].status).toBe("complete");
  });

  it("keeps one disclosure control and one full-copy action in a window starting mid run", () => {
    const messages = createHistoryProjection().project(page([event("middle"), event("tail")]));
    expect(messages.map((message) => message.history?.first)).toEqual([true, false]);
    expect(messages.map((message) => message.history?.last)).toEqual([false, true]);
  });

  it("does not interpret a deferred body as empty text or missing tool success", () => {
    const deferred = { ...event("a"), payload: {}, body: { state: "deferred" as const, ref: "a", byte_length: 1000000 } };
    const projected = projectHistoryEvent(page([deferred]), deferred)!;
    expect(projected.blocks).toEqual([]);
    expect(projected.history?.event.body.state).toBe("deferred");
    const request = { ...event("tool"), type: "tool_call_request", payload: { tool_name: "exec_command", tool_call_id: "x" },
      result_event: { ...deferred, type: "tool_call_result", summary: { status: "exited", exit_code: 2 } } };
    const tool = projectHistoryEvent(page([request]), request)!;
    expect(tool.blocks).toEqual([]);
    expect(tool.history?.event.result_event?.summary?.exit_code).toBe(2);
  });

  it("merges only matching epochs and bounds retained virtual fragments", () => {
    const cache = createSessionHistoryPages();
    cache.accept(page(Array.from({ length: 300 }, (_, i) => event(`new${i}`))));
    const merged = cache.accept(page(Array.from({ length: 300 }, (_, i) => event(`old${i}`))), true);
    expect("history_items" in merged && merged.history_items.length).toBe(512);
    expect("history_items" in merged && merged.has_after).toBe(true);
    const rewrite = cache.accept({ ...page([event("replacement")]), history_epoch: "rewrite" });
    expect("history_items" in rewrite && rewrite.history_items.map((item) => item.uuid)).toEqual(["replacement"]);
  });
});
