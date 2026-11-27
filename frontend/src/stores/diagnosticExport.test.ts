import { expect, it, vi } from "vitest";
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
vi.mock("@/services/diagnostics", () => ({ cancelDiagnosticJob: vi.fn(), getDiagnosticJob: vi.fn() }));
import { acceptDiagnosticJob } from "./diagnosticExport";
import type { DiagnosticJob } from "@/services/diagnostics";

const snapshot = (ordinal: number, version: number, status: DiagnosticJob["status"]): DiagnosticJob => ({
  id: String(ordinal), ordinal, version, status, stage: "archive", processedBytes: 42,
  elapsedMs: 1000, result: null, error: null,
});

it("rejects stale snapshots and events across both task versions and task generations", () => {
  const cancelling = snapshot(2, 4, "cancelling");
  expect(acceptDiagnosticJob(cancelling, snapshot(2, 3, "running"))).toBe(cancelling);
  expect(acceptDiagnosticJob(cancelling, snapshot(1, 100, "complete"))).toBe(cancelling);
  expect(acceptDiagnosticJob(cancelling, snapshot(2, 5, "cancelled")).status).toBe("cancelled");
  expect(acceptDiagnosticJob(cancelling, snapshot(3, 1, "running")).ordinal).toBe(3);
});
