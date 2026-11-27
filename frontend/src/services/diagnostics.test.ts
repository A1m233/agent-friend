import { beforeEach, expect, it, vi } from "vitest";
import { startDiagnosticBundle } from "./diagnostics";

const invoke = vi.hoisted(() => vi.fn().mockResolvedValue({ outcome: "complete" }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
beforeEach(() => invoke.mockClear());

it("defaults to recent history while preserving an explicit recent range", async () => {
  await startDiagnosticBundle({ sessionIds: ["a", "b"] });
  expect(invoke.mock.calls[0][1].request.sessionTurnLimit).toBe(10);
  await startDiagnosticBundle({ sessionIds: ["a"], sessionTurnLimit: null });
  expect(invoke.mock.calls[1][1].request.sessionTurnLimit).toBeNull();
  await startDiagnosticBundle({ sessionIds: ["a"], sessionTurnLimit: 3 });
  expect(invoke.mock.calls[2][1].request.sessionTurnLimit).toBe(3);
});

it("requires a separate opt-in for crash dumps even without selected conversations", async () => {
  await startDiagnosticBundle({ sessionIds: [] });
  expect(invoke.mock.calls[0][1].request.includeCrashDumps).toBe(false);
  await startDiagnosticBundle({ sessionIds: [], includeCrashDumps: true });
  expect(invoke.mock.calls[1][1].request.includeCrashDumps).toBe(true);
  await startDiagnosticBundle({ sessionIds: ["a"], includeAttachmentOriginals: true });
  expect(invoke.mock.calls[2][1].request.includeCrashDumps).toBe(false);
});
