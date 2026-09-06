import { describe, expect, it, vi } from "vitest";
vi.mock("../http", () => ({ http: { post: vi.fn() } }));
import { http } from "../http";
import { runsApi } from "./runs";

describe("precise run cancellation", () => {
  it("passes the known runtime identity without changing the legacy path", () => {
    runsApi.cancel("session/a", "run a", "runtime/a");
    expect(http.post).toHaveBeenLastCalledWith(
      "/v1/sessions/session%2Fa/runs/run%20a/cancel?expectedRuntimeInstanceId=runtime%2Fa",
    );
    runsApi.cancel("session/a", "run a");
    expect(http.post).toHaveBeenLastCalledWith("/v1/sessions/session%2Fa/runs/run%20a/cancel");
  });
});
