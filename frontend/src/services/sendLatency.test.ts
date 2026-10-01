import { afterEach, describe, expect, it, vi } from "vitest";
import { startSendPhase } from "./sendLatency";

describe("send latency", () => {
  afterEach(() => vi.restoreAllMocks());

  it("records only identity, fixed phase, outcome and elapsed time", () => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(performance, "now").mockReturnValueOnce(100).mockReturnValueOnce(125);
    startSendPhase("run-1", "service_ready")("error");
    expect(log).toHaveBeenCalledWith("[send-latency]", {
      runId: "run-1", phase: "service_ready", status: "error", durationMs: 25,
    });
  });

  it("does not turn a logger failure into a send failure", () => {
    vi.spyOn(console, "info").mockImplementation(() => { throw new Error("logger"); });
    expect(() => startSendPhase("run-1", "http_response")("ok")).not.toThrow();
  });
});
