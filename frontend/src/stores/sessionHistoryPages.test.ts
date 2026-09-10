import { describe, expect, it } from "vitest";
import type { SessionDetail, SessionEvent } from "@/types/meta";
import { createSessionHistoryPages } from "./sessionHistoryPages";

const page = (ids: string[], cursor: string | null, epoch = "initial", revision = 1): SessionDetail => ({
  session_id: "one", title: "test", persona: "default", model: "test", revision,
  history_epoch: epoch, history_cursor: cursor,
  active_events: ids.map((uuid) => ({ uuid, type: "user_message", ts: "2026-09-10", payload: { content: uuid } }) as SessionEvent),
});
const ids = (detail: SessionDetail) => detail.active_events?.map((event) => event.uuid);

describe("complete history pages", () => {
  it("prepends old pages and refreshes the tail without duplicates", () => {
    const cache = createSessionHistoryPages();
    cache.accept(page(["3", "4"], "3"));
    expect(ids(cache.accept(page(["1", "2"], null), true))).toEqual(["1", "2", "3", "4"]);
    const refreshed = cache.accept(page(["3", "4", "5"], "3", "initial", 2));
    expect(ids(refreshed)).toEqual(["1", "2", "3", "4", "5"]);
    expect(refreshed.history_cursor).toBeNull();
  });
  it("drops obsolete pages after rewrite and rejects older responses", () => {
    const cache = createSessionHistoryPages();
    cache.accept(page(["old"], null));
    cache.accept(page(["new"], null, "rewrite", 3));
    expect(ids(cache.accept(page(["old"], null)))).toEqual(["new"]);
    cache.prune(new Set());
    expect(cache.get("one")).toBeUndefined();
  });
});
