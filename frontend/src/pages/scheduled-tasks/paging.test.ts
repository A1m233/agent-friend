import { expect, it, vi } from "vitest";
import { loadVisiblePages } from "./paging";

it("retains earlier rows and deduplicates shifting page boundaries", async () => {
  const fetch = vi.fn(async (offset: number) => offset === 0 ? [{ id: "a" }, { id: "b" }] : [{ id: "b" }, { id: "c" }]);
  expect(await loadVisiblePages(fetch, 2, 2)).toEqual({ rows: [{ id: "a" }, { id: "b" }, { id: "c" }], hasMore: true });
  expect(fetch.mock.calls).toEqual([[0], [2]]);
});

it("refresh removes deleted rows and stops at the last partial page", async () => {
  const fetch = vi.fn(async () => [{ id: "remaining" }]);
  expect(await loadVisiblePages(fetch, 3, 2)).toEqual({ rows: [{ id: "remaining" }], hasMore: false });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("does not commit an incomplete window when a later page fails", async () => {
  const fetch = vi.fn().mockResolvedValueOnce([{ id: "a" }]).mockRejectedValueOnce(new Error("offline"));
  await expect(loadVisiblePages(fetch, 2, 1)).rejects.toThrow("offline");
});
