// UI-only fixture in an existing, identity-verified Tauri dev WebView. No model request.
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const require = createRequire(resolve("frontend/package.json"));
const { chromium } = require("playwright");
const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
if (!args.includes("--output-dir")) throw new Error("--output-dir is required");
const output = resolve(option("--output-dir"));
await mkdir(output, { recursive: false });
const browser = await chromium.connectOverCDP(args.includes("--cdp") ? option("--cdp") : "http://127.0.0.1:9222");
let page;
try {
  page = browser.contexts().flatMap((context) => context.pages()).find((candidate) => candidate.url().includes("/chat.html"));
  if (!page) throw new Error("Tauri chat target not found");
  await page.evaluate(async () => {
    if (!window.__TAURI_INTERNALS__) throw new Error("Real Tauri WebView required");
    const { useConversationStore: conversation } = await import("/src/stores/conversation.ts");
    const { useFilesystemApprovalsStore: approvals } = await import("/src/stores/filesystemApprovals.ts");
    const { filesystemApprovalsApi: api } = await import("/src/services/api/filesystem.ts");
    const { sessionsApi } = await import("/src/services/api/sessions.ts");
    const { runsApi } = await import("/src/services/api/runs.ts");
    const before = conversation.getState();
    if (before.currentSessionId || before.messages.length) throw new Error("Open an empty new conversation before running this isolated UI fixture");
    const oldEntries = approvals.getState().entries;
    const oldPending = api.pending;
    const oldDecide = api.decide;
    const created = await sessionsApi.create();
    const oldRunsList = runsApi.list;
    const oldRunsGet = runsApi.get;
    let unsubscribe = () => {};
    window.__imageVisualRestore = async () => {
      unsubscribe();
      runsApi.list = oldRunsList;
      runsApi.get = oldRunsGet;
      api.pending = oldPending;
      api.decide = oldDecide;
      approvals.setState({ entries: oldEntries });
      conversation.setState({ currentSessionId: before.currentSessionId, runSnapshots: before.runSnapshots,
        runCalibrationPhase: before.runCalibrationPhase, approvalMode: before.approvalMode });
      await sessionsApi.delete(created.session_id);
      const { useSessionsStore } = await import("/src/stores/sessions.ts");
      await useSessionsStore.getState().refresh();
      delete window.__imageVisualRestore;
    };
    const { useSessionsStore } = await import("/src/stores/sessions.ts");
    await useSessionsStore.getState().refresh();
    await conversation.getState().openSession(created.session_id);
    const request = {
      request_id: "image-visual-approval", session_id: created.session_id, run_id: "image-visual-run",
      tool_call_id: "image-visual-call", reason: "查看本次任务中的本地图片（视觉验收夹具）",
      items: [{ item_id: "image", path: "C:\\visual-fixture\\diagram.png", root_kind: "file", permissions: ["read_image"] }],
      actions: [], version: 1, status: "pending",
    };
    api.pending = async () => request.status === "pending" ? [request] : [];
    api.decide = async (_request, approved) => {
      if (approved) throw new Error("This fixture only permits rejection");
      request.status = "denied";
      request.version += 1;
      approvals.getState().merge({ ...request });
    };
    conversation.setState({ currentSessionId: request.session_id, runCalibrationPhase: "ready", approvalMode: "per_request",
      runSnapshots: { [request.run_id]: { run_id: request.run_id, session_id: request.session_id, status: "running",
        outcome: null, reason_code: "", channel: "text", origin: "user", control_scope: "desktop_text",
        created_at: new Date().toISOString(), started_at: new Date().toISOString(), finished_at: null, version: 1 } } });
    const fixtureRun = conversation.getState().runSnapshots[request.run_id];
    unsubscribe = conversation.subscribe((state) => {
      if (state.currentSessionId === request.session_id && !state.runSnapshots[request.run_id]) {
        conversation.setState({ runSnapshots: { ...state.runSnapshots, [request.run_id]: fixtureRun } });
      }
    });
    runsApi.list = async () => ({ runs: [fixtureRun] });
    runsApi.get = async () => fixtureRun;
    approvals.getState().merge(request);
  });
  const dock = page.locator('[data-primary-activity="approval"]');
  await dock.getByText("范围：C:\\visual-fixture\\diagram.png（读取图片）", { exact: true }).waitFor({ timeout: 5000 });
  const text = await dock.innerText();
  await page.screenshot({ path: resolve(output, "permission.png") });
  const button = dock.getByRole("button", { name: "拒绝", exact: true });
  const hit = await button.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { width: rect.width, height: rect.height,
      hit: element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)) };
  });
  if (!hit.hit) throw new Error("Rejection button obscured");
  await button.click();
  await dock.waitFor({ state: "hidden" });
  await writeFile(resolve(output, "report.json"), JSON.stringify({
    evidence: "desktop DOM via CDP; UI-only state fixture", url: page.url(), text, rejectionButton: hit,
    rejectedUiHidden: true, modelRequests: 0, backendApprovalVerified: false,
  }, null, 2));
  console.log(JSON.stringify({ output, passed: true, evidence: "desktop DOM via CDP; UI-only fixture" }));
} catch (error) {
  if (page) {
    await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
    console.log(await page.evaluate(async () => {
      const { useConversationStore } = await import("/src/stores/conversation.ts");
      const { useFilesystemApprovalsStore } = await import("/src/stores/filesystemApprovals.ts");
      const state = useConversationStore.getState();
      return { session: state.currentSessionId, runs: state.runSnapshots, approvals: useFilesystemApprovalsStore.getState().entries,
        activity: [...document.querySelectorAll("[data-primary-activity]")].map((e) => e.textContent) };
    }));
  }
  throw error;
} finally {
  if (page) await page.evaluate(() => window.__imageVisualRestore?.()).catch(() => {});
  await browser.close();
}
