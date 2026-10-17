import { afterEach, expect, it, vi } from "vitest";
import { logRunStop, stopErrorCode } from "./diagnostics";

afterEach(() => vi.restoreAllMocks());

it("logs only approved metadata and excludes arbitrary error details", () => {
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  const input = { sessionId: "s", runId: "r", text: "private message", error: new Error("secret") };
  logRunStop("button_click", input);
  expect(log).toHaveBeenCalledWith("[run-stop]", expect.objectContaining({ event: "button_click", runId: "r" }));
  expect(JSON.stringify(log.mock.calls)).not.toMatch(/private message|secret/);
  expect(stopErrorCode({ code: "network", message: "secret" })).toBe("network");
  expect(stopErrorCode({ code: "private message" })).toBe("unknown");
  expect(stopErrorCode(new Error("secret"))).toBe("unknown");
});

it("does not let a logging failure prevent a stop", () => {
  vi.spyOn(console, "info").mockImplementation(() => { throw new Error("logger failed"); });
  expect(() => logRunStop("decision", { result: "request_cancel" })).not.toThrow();
});
