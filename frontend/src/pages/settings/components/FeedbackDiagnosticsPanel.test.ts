// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { SessionSummary } from "@/types/meta";

const mocks = vi.hoisted(() => ({
  generate: vi.fn(),
  renderStatus: vi.fn(),
  sessions: { list: [] as SessionSummary[], loading: false, loaded: false, refresh: vi.fn().mockResolvedValue(undefined) },
  archived: { list: [] as SessionSummary[], loading: false, loaded: false, refresh: vi.fn().mockResolvedValue(undefined) },
}));
vi.mock("@/stores/diagnosticExport", () => ({
  useDiagnosticExportStore: (select: (state: unknown) => unknown) => select({ job: null, error: null, cancel: vi.fn() }),
  startDiagnosticExportSync: () => () => {},
}));
vi.mock("@/stores", () => ({
  useSessionsStore: (select: (state: typeof mocks.sessions) => unknown) => select(mocks.sessions),
  useArchivedSessionsStore: (select: (state: typeof mocks.archived) => unknown) => select(mocks.archived),
}));
vi.mock("@/services", () => ({
  generateDiagnosticBundle: mocks.generate,
  getRenderEvidenceStatus: mocks.renderStatus,
  captureRenderEvidenceNow: vi.fn(),
  clearRenderEvidence: vi.fn(),
  setRenderEvidenceArmed: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-opener", () => ({ revealItemInDir: vi.fn() }));
vi.mock("./PetRenderEvidencePanel", () => ({ PetRenderEvidencePanel: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));
import { toast } from "sonner";
import { DiagnosticExportCancelled } from "@/services/diagnostics";
import { FeedbackDiagnosticsPanel } from "./FeedbackDiagnosticsPanel";

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  mocks.sessions.list = [];
  mocks.sessions.loading = false;
  mocks.sessions.loaded = false;
  mocks.archived.list = [];
  mocks.archived.loading = false;
  mocks.archived.loaded = false;
  mocks.renderStatus.mockResolvedValue({ supported: false });
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

async function toggleAdvanced() {
  await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="高级选项"]')!.click());
}

function addSession() {
  mocks.sessions.list = [{
    session_id: "session-1", title: "待排查会话", updated_at: "2026-11-28T01:00:00Z",
  } as SessionSummary];
  mocks.sessions.loaded = true;
}

it.each(["sessions", "archived"] as const)("keeps cached %s rows mounted and selected during background refresh", async (source) => {
  const store = mocks[source];
  store.list = [{ session_id: "session-1", title: "待排查会话", updated_at: "2026-11-28T01:00:00Z" } as SessionSummary];
  store.loaded = true;
  const prefill = { requestId: 1, sessionId: "session-1" };
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill })));
  const checkbox = container.querySelector('[aria-label="选择会话 待排查会话"]')!;
  expect(checkbox.getAttribute("aria-checked")).toBe("true");
  const description = await editDescription("保留正在填写的说明");
  store.loading = true;
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill })));
  expect(container.querySelector('[aria-label="选择会话 待排查会话"]')).toBe(checkbox);
  expect(container.textContent).not.toContain("正在加载会话");
  expect(description.value).toBe("保留正在填写的说明");
  store.list = [{ ...store.list[0], title: "已更新的会话" }];
  store.loading = false;
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill })));
  expect(container.querySelector('[aria-label="选择会话 已更新的会话"]')).toBe(checkbox);
  expect(checkbox.getAttribute("aria-checked")).toBe("true");
});

it.each(["sessions", "archived"] as const)("shows loading only for initial empty %s data, not a known empty list refreshing", async (source) => {
  mocks[source].loading = true;
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  expect(container.textContent).toContain("正在加载会话");
  mocks[source].loading = false;
  mocks[source].loaded = true;
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  expect(container.textContent).toContain("暂无可导出的已持久化会话");
  mocks[source].loading = true;
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  expect(container.textContent).not.toContain("正在加载会话");
  expect(container.textContent).toContain("暂无可导出的已持久化会话");
});

it("keeps available cached sessions while the archive is still loading for the first time", async () => {
  addSession();
  mocks.archived.loading = true;
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  expect(container.querySelector('[aria-label="选择会话 待排查会话"]')).not.toBeNull();
  expect(container.textContent).not.toContain("正在加载会话");
});

it("exports the selected recent turns without opening optional sections", async () => {
  addSession();
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: { requestId: 1, sessionId: "session-1" } })));
  const advanced = container.querySelector('[aria-label="高级选项"]')!;
  expect(advanced.getAttribute("aria-expanded")).toBe("false");
  expect(container.querySelector('[aria-label="收集全部已保留日志"]')!.closest("[hidden]")).not.toBeNull();
  expect(container.querySelector('[aria-label="问题说明"]')).not.toBeNull();
  expect(container.querySelector('[aria-label="问题说明"]')!.closest("[hidden]")).toBeNull();
  const turns = container.querySelector<HTMLInputElement>('[aria-label="采集最近轮数"]')!;
  expect(turns.closest("[hidden]")).toBeNull();
  expect(turns.value).toBe("10");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(turns, "5");
    turns.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await generate();
  expect(mocks.generate).toHaveBeenCalledWith({
    sessionIds: ["session-1"], sessionTurnLimit: 5, description: undefined,
    includeAttachmentOriginals: false, includeCrashDumps: false, includeRenderEvidence: false,
    allRetainedLogs: false, verifyInstallFiles: false,
  });
  expect(advanced.getAttribute("aria-expanded")).toBe("false");
});

it("preserves advanced choices when collapsed and keeps their summary visible", async () => {
  addSession();
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: { requestId: 1, sessionId: "session-1" } })));
  await toggleAdvanced();
  for (const label of ["收集全部已保留日志", "校验安装文件完整性", "包含相关原始附件", "包含原始崩溃转储"]) {
    await act(async () => container.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!.click());
  }
  await toggleAdvanced();
  const summary = [...container.querySelectorAll("p")].find((item) => item.textContent?.startsWith("高级选项已开启"))!;
  expect(summary.closest("[hidden]")).toBeNull();
  expect(summary.textContent).toContain("全部日志、安装文件校验、附件原件、原始崩溃转储");
  await generate();
  expect(mocks.generate).toHaveBeenCalledWith(expect.objectContaining({
    allRetainedLogs: true, verifyInstallFiles: true, includeAttachmentOriginals: true, includeCrashDumps: true,
  }));
  await toggleAdvanced();
  expect(container.querySelector('[aria-label="收集全部已保留日志"]')!.getAttribute("aria-checked")).toBe("true");
  expect(container.querySelector('[aria-label="包含原始崩溃转储"]')!.getAttribute("aria-checked")).toBe("false");
});

it("keeps automatic desktop screenshot disclosure visible outside advanced options", async () => {
  mocks.renderStatus.mockResolvedValue({ supported: true, pending: { automaticIncident: true, complete: true, capturedAt: "2026-11-28T01:00:00Z" } });
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  const warning = [...container.querySelectorAll("p")].find((item) => item.textContent?.startsWith("本次会包含完整桌面截图"))!;
  expect(warning).toBeDefined();
  expect(warning.closest("[hidden]")).toBeNull();
  expect(container.querySelector('[aria-label="高级选项"]')!.getAttribute("aria-expanded")).toBe("false");
});

it("keeps turn validation and all-turn export available in the main flow", async () => {
  addSession();
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: { requestId: 1, sessionId: "session-1" } })));
  const turns = container.querySelector<HTMLInputElement>('[aria-label="采集最近轮数"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(turns, "0");
    turns.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await generate();
  expect(mocks.generate).not.toHaveBeenCalled();
  expect(container.querySelector('[role="alert"]')?.textContent).toBe("请输入 1～100 的整数。");
  await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="采集全部轮次"]')!.click());
  expect(turns.disabled).toBe(true);
  await generate();
  expect(mocks.generate).toHaveBeenCalledWith(expect.objectContaining({ sessionIds: ["session-1"], sessionTurnLimit: null }));
});

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

it("keeps the description without an error toast after user cancellation", async () => {
  mocks.generate.mockRejectedValue(new DiagnosticExportCancelled());
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  const input = await editDescription("取消后保留");
  await generate();
  expect(input.value).toBe("取消后保留");
  expect(toast.error).not.toHaveBeenCalled();
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
  await toggleAdvanced();
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

it.each([historyWarning, { collection: "temporary-files", message: "临时文件清理失败" }])("keeps collection failures prominent when warnings also exist: %s", async (warning) => {
  mocks.generate.mockResolvedValue({
    path: "diagnostics.zip", outcome: "partial", includedSessionIds: [],
    failures: [{ collection: "logs", message: "日志读取失败" }], warnings: [warning],
  });
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  await generate();
  expect(container.textContent).toContain("诊断包已生成，但部分内容获取失败");
  expect(container.textContent).toContain("日志读取失败");
  expect(container.textContent).toContain(warning.message);
  expect(toast.warning).toHaveBeenCalledWith("诊断包已生成，但部分内容获取失败", expect.anything());
  expect(toast.success).not.toHaveBeenCalled();
});

it("accepts older successful results without a warnings field", async () => {
  await act(async () => root.render(createElement(FeedbackDiagnosticsPanel, { prefill: null })));
  await generate();
  expect(toast.success).toHaveBeenCalledWith("诊断包已生成", expect.anything());
  expect(container.textContent).not.toContain("历史证据说明");
});
