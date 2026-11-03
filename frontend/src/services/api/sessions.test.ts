import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../http", () => ({
  http: {
    get: vi.fn(),
  },
}));

import { http } from "../http";
import { sessionsApi } from "./sessions";

describe("sessionsApi.get", () => {
  beforeEach(() => {
    vi.mocked(http.get).mockReset();
  });

  it("requests only active events and encodes the session id", () => {
    sessionsApi.get("im:qq/group 1");

    expect(http.get).toHaveBeenCalledWith(
      "/v1/sessions/im%3Aqq%2Fgroup%201?view=active&turn_limit=30",
    );
  });

  it("requests the preceding complete-turn page with its original epoch", () => {
    sessionsApi.getOlder("im:qq/group 1", "turn:31/user", "epoch 1");

    expect(http.get).toHaveBeenCalledWith(
      "/v1/sessions/im%3Aqq%2Fgroup%201?view=active&turn_limit=30&before=turn%3A31%2Fuser&history_epoch=epoch+1",
    );
  });

  it("retries a latest-page history conflict once with the same encoded identity", async () => {
    const detail = { session_id: "im:qq/group 1", active_events: [] };
    vi.mocked(http.get).mockRejectedValueOnce({ code: "history_changed" })
      .mockResolvedValueOnce(detail);
    await expect(sessionsApi.get("im:qq/group 1")).resolves.toBe(detail);
    expect(http.get).toHaveBeenCalledTimes(2);
    expect(vi.mocked(http.get).mock.calls[1]).toEqual(vi.mocked(http.get).mock.calls[0]);
  });

  it("stops after a second conflict and permits a later explicit retry", async () => {
    const error = { code: "history_changed" };
    vi.mocked(http.get).mockRejectedValue(error);
    await expect(sessionsApi.get("sid")).rejects.toBe(error);
    expect(http.get).toHaveBeenCalledTimes(2);
    const detail = { session_id: "sid", active_events: [] };
    vi.mocked(http.get).mockResolvedValueOnce(detail);
    await expect(sessionsApi.get("sid")).resolves.toBe(detail);
    expect(http.get).toHaveBeenCalledTimes(3);
  });

  it.each(["network", "http_error", "session_busy"])("does not retry %s errors", async (code) => {
    const error = { code };
    vi.mocked(http.get).mockRejectedValue(error);
    await expect(sessionsApi.get("sid")).rejects.toBe(error);
    expect(http.get).toHaveBeenCalledTimes(1);
  });

  it("leaves an older-page epoch conflict to its navigation owner", async () => {
    const error = { code: "history_changed" };
    vi.mocked(http.get).mockRejectedValue(error);
    await expect(sessionsApi.getOlder("sid", "cursor", "epoch")).rejects.toBe(error);
    expect(http.get).toHaveBeenCalledTimes(1);
  });
});

describe("sessionsApi.search", () => {
  beforeEach(() => {
    vi.mocked(http.get).mockReset();
  });

  it("encodes the query and bounded result limits", () => {
    sessionsApi.search("  中文 & Needle  ", { messageLimit: 12, titleLimit: 4 });

    expect(http.get).toHaveBeenCalledWith(
      "/v1/sessions/search?q=%E4%B8%AD%E6%96%87+%26+Needle&message_limit=12&title_limit=4",
    );
  });
});
