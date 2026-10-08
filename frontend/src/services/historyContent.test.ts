import { beforeEach, expect, it, vi } from "vitest";
import type { HistoryEvent, HistoryFragment, HistoryPage } from "@/types/history";
vi.mock("./api/sessions", () => ({ sessionsApi: { history: vi.fn(), historyContent: vi.fn() } }));
import { sessionsApi } from "./api/sessions";
import { readHistoryCopy, readHistoryEvent } from "./historyContent";

const item: HistoryEvent = { uuid: "text", type: "assistant_message", ts: "2026-10-08", message_id: "logical",
  payload: {}, body: { state: "deferred", ref: "text", byte_length: 9999 } };
const fragment: HistoryFragment = { sessionId: "s", messageId: "logical", version: "v", epoch: "e",
  event: item, first: true, last: true };
const page = (events: HistoryEvent[], more = false): HistoryPage => ({ session_id: "s", title: "s", model: "m", persona: "p",
  content_version: "v", history_epoch: "e", history_items: events, before_cursor: null,
  after_cursor: events.at(-1)?.uuid ?? null, has_before: false, has_after: more });
beforeEach(() => vi.clearAllMocks());

it("joins version-bound complete event chunks without dropping unicode text", async () => {
  const raw = JSON.stringify({ ...item, payload: { content: "中文😀" } });
  vi.mocked(sessionsApi.historyContent).mockResolvedValueOnce({ text: raw.slice(0, 17), offset: 0,
    next_offset: 17, total_chars: raw.length, complete: false, content_version: "v" })
    .mockResolvedValueOnce({ text: raw.slice(17), offset: 17, next_offset: raw.length,
      total_chars: raw.length, complete: true, content_version: "v" });
  const full = await readHistoryEvent("s", "v", item);
  expect(full.payload?.content).toBe("中文😀");
  expect(full.body.state).toBe("inline");
});

it("copies the entire logical message in order without fetching tool bodies", async () => {
  const text = (id: string): HistoryEvent => ({ ...item, uuid: id, payload: { content: id }, body: { state: "inline" } });
  vi.mocked(sessionsApi.history).mockResolvedValueOnce(page([text("first"), { ...item, type: "tool_call_request" }], true))
    .mockResolvedValueOnce(page([text("last")]));
  expect(await readHistoryCopy(fragment)).toBe("first\n\nlast");
  expect(sessionsApi.historyContent).not.toHaveBeenCalled();
  expect(sessionsApi.history).toHaveBeenLastCalledWith("s", expect.objectContaining({ after: "text", messageId: "logical" }));
});

it("rejects a changed version rather than returning a partial copy", async () => {
  vi.mocked(sessionsApi.history).mockResolvedValueOnce(page([{ ...item, body: { state: "inline" }, payload: { content: "first" } }], true))
    .mockResolvedValueOnce({ ...page([]), content_version: "changed" });
  await expect(readHistoryCopy(fragment)).rejects.toThrow("历史已更新");
});
