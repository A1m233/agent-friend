// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  generate: vi.fn(),
  sessions: { list: [], loading: false, refresh: vi.fn().mockResolvedValue(undefined) },
}));
vi.mock("@/stores", () => ({
  useSessionsStore: (select: (state: typeof mocks.sessions) => unknown) => select(mocks.sessions),
  useArchivedSessionsStore: (select: (state: typeof mocks.sessions) => unknown) => select(mocks.sessions),
}));
vi.mock("@/services", () => ({
  generateDiagnosticBundle: mocks.generate,
  getRenderEvidenceStatus: vi.fn().mockResolvedValue({ supported: false }),
  captureRenderEvidenceNow: vi.fn(),
  clearRenderEvidence: vi.fn(),
  setRenderEvidenceArmed: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-opener", () => ({ revealItemInDir: vi.fn() }));
vi.mock("./PetRenderEvidencePanel", () => ({ PetRenderEvidencePanel: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));
import { FeedbackDiagnosticsPanel } from "./FeedbackDiagnosticsPanel";

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  mocks.generate.mockReset().mockResolvedValue({
    path: "diagnostics.zip", outcome: "complete", failures: [], includedSessionIds: [],
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function generate() {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent === "生成诊断包");
  expect(button).toBeDefined();
  await act(async () => button!.click());
}

it("exports summaries by default and opts into dumps independently of conversations", async () => {
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  const checkbox = container.querySelector<HTMLButtonElement>('[aria-label="包含原始崩溃转储"]');
  expect(checkbox?.getAttribute("aria-checked")).toBe("false");
  await generate();
  expect(mocks.generate).toHaveBeenLastCalledWith(expect.objectContaining({
    sessionIds: [], includeCrashDumps: false,
  }));
  await act(async () => checkbox!.click());
  await generate();
  expect(mocks.generate).toHaveBeenLastCalledWith(expect.objectContaining({
    sessionIds: [], includeCrashDumps: true,
  }));
  expect(checkbox?.getAttribute("aria-checked")).toBe("false");
  await act(async () => root.render(null));
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  expect(container.querySelector('[aria-label="包含原始崩溃转储"]')?.getAttribute("aria-checked")).toBe("false");
});

it("shows partial crash collection and its actionable failure in the export result", async () => {
  mocks.generate.mockResolvedValue({
    path: "diagnostics.zip", outcome: "partial", includedSessionIds: [],
    failures: [{ collection: "native-crash", message: "崩溃记录采集或导出不完整（watcher-handshake-failed）" }],
  });
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  await generate();
  expect(container.textContent).toContain("诊断包已生成，但部分内容获取失败");
  expect(container.textContent).toContain("崩溃记录采集或导出不完整（watcher-handshake-failed）");
});
