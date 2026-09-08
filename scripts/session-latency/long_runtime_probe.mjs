// Explicitly authorized live model test against an isolated, budgeted backend.
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { readFile, writeFile } from "node:fs/promises";
const require = createRequire(resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/package.json"));
const { chromium } = require("playwright");
const directory = process.argv[2];
if (!directory) throw new Error("Requires the explicitly authorized isolated runtime directory");
const output = resolve(directory, process.argv[3] ?? "desktop-run.json");
const identity = JSON.parse(await readFile(resolve(directory, "acceptance-identity.json"), "utf8"));
const initialBudget = JSON.parse(await readFile(resolve(directory, "budget.json"), "utf8"));
const runtimeLog = await readFile(resolve(directory, "logs/agent_bridge.log"), "utf8");
const expectedRuntime = runtimeLog.match(/bridge_runtime_started runtime_instance_id=(\S+)/)?.[1];
if (!expectedRuntime) throw new Error("Isolated runtime identity missing from its startup log");
if (initialBudget.reservedRequests !== 0) throw new Error("This runtime's real-call allowance was already used");
await writeFile(output, JSON.stringify({ status: "reserved" }), { flag: "wx" });
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
const page = browser.contexts().flatMap(c => c.pages()).find(p => new URL(p.url()).pathname === "/chat.html");
const matcher = url => ["http://localhost:1420", "http://127.0.0.1:18800"].includes(url.origin)
  && (url.pathname.startsWith("/v1/") || url.pathname.startsWith("/ag-ui/") || url.pathname === "/healthz");
const handler = route => {
  const url = new URL(route.request().url());
  url.hostname = "127.0.0.1";
  url.port = String(identity.port);
  return route.continue({ url: url.toString() });
};
const report = { started: new Date().toISOString(), transitions: [], requests: [] };
page.setDefaultTimeout(15000);
page.on("requestfinished", r => {
  if (r.url().includes(":18820/") || matcher(new URL(r.url()))) report.requests.push({
    path: new URL(r.url()).pathname, method: r.method(), timing: r.timing() });
});
try {
  await page.route(matcher, handler);
  await page.reload();
  await page.evaluate(async () => { window.__longStore = (await import("/src/stores/conversation.ts")).useConversationStore; });
  const listing = await page.evaluate(async () => await (await fetch("http://127.0.0.1:18820/v1/sessions")).json());
  const longTitle = listing.find(s => s.session_id === identity.sessionId)?.title;
  if (!longTitle) throw new Error("Isolated session absent from listing");
  await page.locator('[data-sidebar="menu-button"]').filter({ hasText: longTitle }).click();
  await page.waitForFunction(() => !window.__longStore.getState().historyLoading
    && window.__longStore.getState().messages.length > 900, null, { timeout: 45000 });
  report.initial = await page.evaluate(() => {
    const s = window.__longStore.getState();
    return { id: s.currentSessionId, runtime: s.runtimeInstanceId, messages: s.messages.length };
  });
  const health = await page.evaluate(async () => await (await fetch("http://127.0.0.1:18820/healthz")).json());
  report.health = health;
  if (report.initial.id !== identity.sessionId || report.initial.runtime !== expectedRuntime) {
    throw new Error("Not attached to the isolated session runtime");
  }
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /当前模型/ }).click();
  await page.getByRole("menuitem", { name: /^模型/ }).hover();
  await page.getByRole("menuitem", { name: "deepseek/deepseek-v4-flash", exact: true }).click();
  await page.waitForFunction(() => window.__longStore.getState().currentModel === "deepseek/deepseek-v4-flash"
    && !window.__longStore.getState().modelSwitching);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  report.notificationCount = await page.evaluate(async () =>
    (await (await fetch("/v1/notifications")).json()).notifications.length);
  report.config = await page.evaluate(() => ({ model: window.__longStore.getState().currentModel,
    reasoning: window.__longStore.getState().currentReasoningPreset }));
  if (report.config.reasoning !== "off") throw new Error("Expected reasoning off");
  await page.locator("textarea").fill("这是隔离环境中的需求120验收，之前的任务都不要继续。只调用一次calculate工具计算17*23，不调用任何其他工具，不修改任何文件。得到结果后只用一句话报告结果并结束。");
  report.submitAt = Date.now();
  await page.locator("textarea").press("Enter");
  await page.waitForFunction(id => Object.values(window.__longStore.getState().runSnapshots)
    .some(r => r.session_id === id && r.status !== "terminal"), identity.sessionId);
  report.run = await page.evaluate(id => Object.values(window.__longStore.getState().runSnapshots)
    .find(r => r.session_id === id && r.status !== "terminal"), identity.sessionId);
  // A running snapshot also covers cold preparation. Wait for the real model
  // request boundary, then record exact switch times for backend-log correlation.
  const marker = `event=llm_stream_start run_id=${report.run.run_id} `;
  const executionDeadline = Date.now() + 90000;
  while (!(await readFile(resolve(directory, "logs/agent_bridge.log"), "utf8")).includes(marker)) {
    if (Date.now() > executionDeadline) throw new Error("No model execution boundary observed");
    await page.waitForTimeout(100);
  }
  report.executionObservedAt = Date.now();
  for (let index = 0; index < 6; index++) {
    const title = index % 2 === 0 ? "会话 2026-09-05 22:46" : longTitle;
    const started = Date.now();
    const runStatusBefore = await page.evaluate(id => window.__longStore.getState().runSnapshots[id]?.status, report.run.run_id);
    await page.locator('[data-sidebar="menu-button"]').filter({ hasText: title }).click();
    await page.waitForFunction(long => {
      const s = window.__longStore.getState();
      return !s.historyLoading && s.messages.length > 0
        && (long ? s.messages.length > 900 : s.messages.length === 2);
    }, index % 2 === 1, { timeout: 45000 });
    report.transitions.push({ target: index % 2 === 0 ? "short" : "long", startedAt: started,
      readyAt: Date.now(), runStatusBefore, readyMs: Date.now() - started,
      runStatus: await page.evaluate(id => window.__longStore.getState().runSnapshots[id]?.status, report.run.run_id) });
    await page.evaluate(async () => { await fetch("/v1/notifications"); });
  }
  await page.waitForFunction(id => window.__longStore.getState().runSnapshots[id]?.status === "terminal",
    report.run.run_id, { timeout: 90000 });
  report.doneAt = Date.now();
  report.terminal = await page.evaluate(async id => await (await fetch("/v1/runs/" + id)).json(), report.run.run_id);
  if (report.terminal.outcome !== "completed") throw new Error("Long-session acceptance did not complete successfully");
  // Terminal state precedes the asynchronous history projection refresh.
  await page.waitForFunction(id => window.__longStore.getState().messages
    .filter(message => message.runId === id).some(message => message.blocks.some(block =>
      block.kind === "tool" && block.name === "calculate" && block.status === "done")),
    report.run.run_id, { timeout: 15000 });
  report.resultObservedAt = Date.now();
  report.result = await page.evaluate(id => window.__longStore.getState().messages.filter(m => m.runId === id)
    .map(m => ({ status: m.status, blocks: m.blocks })), report.run.run_id);
  // Permit bounded background completion; upstream quota also covers these workers.
  await page.waitForTimeout(8000);
  report.budget = JSON.parse(await readFile(resolve(directory, "budget.json"), "utf8"));
  const toolBlocks = report.result.flatMap(message => message.blocks).filter(block => block.kind === "tool");
  if (toolBlocks.length !== 1 || toolBlocks[0].name !== "calculate" || toolBlocks[0].status !== "done"
    || String(JSON.parse(toolBlocks[0].result).result) !== "391") throw new Error("Expected exactly one successful calculate result of 391");
  if (!report.transitions.some(item => item.runStatusBefore === "running" && item.runStatus === "running")) {
    throw new Error("No session switch completed during model execution");
  }
} catch (error) {
  report.error = String(error);
  throw error;
} finally {
  await writeFile(output, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, requests: report.requests.length, result: undefined }));
  await page.unroute(matcher, handler);
  await page.reload();
  await browser.close();
}
