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
import { toast } from "sonner";
import { FeedbackDiagnosticsPanel } from "./FeedbackDiagnosticsPanel";

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  vi.clearAllMocks();
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

async function editDescription(value: string) {
  const input = container.querySelector<HTMLTextAreaElement>('[aria-label="问题说明"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}

it.each(["complete", "partial"])("clears the submitted description after a %s export", async (outcome) => {
  mocks.generate.mockResolvedValue({
    path: "diagnostics.zip", outcome, failures: [], includedSessionIds: [],
  });
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  const input = await editDescription("  导出前的问题说明  ");
  await generate();
  expect(mocks.generate).toHaveBeenLastCalledWith(expect.objectContaining({ description: "导出前的问题说明" }));
  expect(input.value).toBe("");
  await generate();
  expect(mocks.generate).toHaveBeenLastCalledWith(expect.objectContaining({ description: undefined }));
});

it("keeps the description when bundle generation fails", async () => {
  mocks.generate.mockRejectedValue(new Error("export failed"));
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  const input = await editDescription("保留以便重试");
  await generate();
  expect(input.value).toBe("保留以便重试");
  expect(toast.error).toHaveBeenCalledWith("无法生成诊断包", { description: "export failed" });
});

it.each(["新问题说明", "原问题说明"])("preserves edits during export even when the final text is %s", async (latest) => {
  let finish!: (value: unknown) => void;
  mocks.generate.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  const input = await editDescription("原问题说明");
  await generate();
  expect(mocks.generate).toHaveBeenCalledWith(expect.objectContaining({ description: "原问题说明" }));
  await editDescription("编辑中的说明");
  await editDescription(latest);
  await act(async () => finish({
    path: "diagnostics.zip", outcome: "complete", failures: [], includedSessionIds: [],
  }));
  expect(input.value).toBe(latest);
  await generate();
  expect(mocks.generate).toHaveBeenLastCalledWith(expect.objectContaining({ description: latest }));
  expect(input.value).toBe("");
});

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

const historyWarning = {
  collection: "native-crash",
  message: "历史运行的退出记录不完整，无法确定当时的退出原因；本次已导出保留的记录",
};

it("reports successful export with historical evidence gaps in both toast and result", async () => {
  mocks.generate.mockResolvedValue({
    path: "diagnostics.zip", outcome: "complete", includedSessionIds: [],
    failures: [], warnings: [historyWarning],
  });
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  await generate();
  expect(container.textContent).toContain("诊断包已生成，部分历史证据不完整");
  expect(container.textContent).toContain(historyWarning.message);
  expect(container.textContent).not.toContain("部分内容获取失败");
  expect(toast.success).toHaveBeenCalledWith("诊断包已生成，部分历史证据不完整", expect.anything());
  expect(toast.warning).not.toHaveBeenCalled();
});

it("keeps collection failures prominent when history warnings also exist", async () => {
  mocks.generate.mockResolvedValue({
    path: "diagnostics.zip", outcome: "partial", includedSessionIds: [],
    failures: [{ collection: "logs", message: "日志读取失败" }], warnings: [historyWarning],
  });
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  await generate();
  expect(container.textContent).toContain("诊断包已生成，但部分内容获取失败");
  expect(container.textContent).toContain("日志读取失败");
  expect(container.textContent).toContain(historyWarning.message);
  expect(toast.warning).toHaveBeenCalledWith("诊断包已生成，但部分内容获取失败", expect.anything());
  expect(toast.success).not.toHaveBeenCalled();
});

it("accepts older successful results without a warnings field", async () => {
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  await generate();
  expect(toast.success).toHaveBeenCalledWith("诊断包已生成", expect.anything());
  expect(container.textContent).not.toContain("历史证据说明");
});
