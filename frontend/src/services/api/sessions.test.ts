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
      "/v1/sessions/im%3Aqq%2Fgroup%201?view=active",
    );
  });
});
