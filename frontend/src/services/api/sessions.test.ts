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
