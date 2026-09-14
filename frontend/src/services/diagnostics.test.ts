import { beforeEach, expect, it, vi } from "vitest";
import { generateDiagnosticBundle } from "./diagnostics";

const invoke = vi.hoisted(() => vi.fn().mockResolvedValue({ outcome: "complete" }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
beforeEach(() => invoke.mockClear());

it("defaults to bounded history while preserving an explicit all-turn selection", async () => {
  await generateDiagnosticBundle({ sessionIds: ["a", "b"] });
  expect(invoke.mock.calls[0][1].request.sessionTurnLimit).toBe(10);
  await generateDiagnosticBundle({ sessionIds: ["a"], sessionTurnLimit: null });
  expect(invoke.mock.calls[1][1].request.sessionTurnLimit).toBeNull();
  await generateDiagnosticBundle({ sessionIds: ["a"], sessionTurnLimit: 3 });
  expect(invoke.mock.calls[2][1].request.sessionTurnLimit).toBe(3);
});
