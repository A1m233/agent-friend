// Explicitly authorized desktop acceptance. One send; approve only the exact target.
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { readFile, writeFile } from "node:fs/promises";
const require = createRequire(resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/package.json"));
const { chromium } = require("playwright");
const [configPath, authorization] = process.argv.slice(2);
if (authorization !== "--real-calls-authorized") throw new Error("Requires explicit authorization for model/background calls and the configured application launch");
const config = JSON.parse(await readFile(configPath, "utf8"));
if (!["accept", "deny", "cancel"].includes(config.mode)) throw new Error("Invalid acceptance mode");
if (typeof config.message !== "string" || typeof config.executable_path !== "string"
  || (config.arguments && (!Array.isArray(config.arguments) || config.arguments.some(a => typeof a !== "string")))) {
  throw new Error("Invalid acceptance target");
}
await writeFile(config.output, JSON.stringify({ status: "reserved" }), { flag: "wx" });
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
const report = { started: new Date().toISOString(), mode: config.mode };
let page;
try {
  const pages = browser.contexts().flatMap(c => c.pages());
  page = pages.find(p => new URL(p.url()).pathname === "/chat.html");
  if (!page && pages.length) {
    await pages[0].evaluate(async () => (await import("/node_modules/.vite/deps/@tauri-apps_api_core.js")).invoke("open_chat"));
    await new Promise(r => setTimeout(r, 1200));
    page = browser.contexts().flatMap(c => c.pages()).find(p => new URL(p.url()).pathname === "/chat.html");
  }
  if (!page || !await page.evaluate(() => Boolean(window.__TAURI_INTERNALS__))) throw new Error("Real Tauri chat required");
  page.setDefaultTimeout(20000);
  await page.evaluate(async () => {
    const s = (await import("/src/stores/conversation.ts")).useConversationStore;
    if (Object.values(s.getState().runSnapshots).some(r => r.status !== "terminal")) throw new Error("Active run must not be interrupted by setup");
    s.getState().newSession();
    await s.getState().selectRuntimeConfig({ model: "deepseek/deepseek-flash", reasoningPreset: "off" });
    await s.getState().setApprovalMode("per_request", false);
    window.__appAcceptance = s;
    window.__appApprovals = (await import("/src/stores/commandRuntime.ts")).useCommandRuntimeStore;
  });
  await page.locator("textarea").fill(config.message);
  await page.locator("textarea").press("Enter");
  await page.waitForFunction(() => Boolean(window.__appAcceptance.getState().currentSessionId), null, { timeout: 30000 });
  report.sessionId = await page.evaluate(() => window.__appAcceptance.getState().currentSessionId);
  await page.waitForFunction(id => Object.values(window.__appApprovals.getState().approvals)
    .some(a => a.session_id === id && a.status === "pending"), report.sessionId, { timeout: 90000 });
  const approval = await page.evaluate(id => Object.values(window.__appApprovals.getState().approvals)
    .find(a => a.session_id === id && a.status === "pending"), report.sessionId);
  if (approval.kind !== "desktop_application" || approval.details.executable_path !== config.executable_path
    || JSON.stringify(approval.details.arguments) !== JSON.stringify(config.arguments || [])) throw new Error("Unexpected approval target");
  report.approval = approval;
  report.approvalText = await page.getByText("打开桌面应用", { exact: true }).first().textContent();
  await page.screenshot({ path: config.output + ".approval.png" });
  await page.getByRole("button", { name: config.mode === "deny" ? "拒绝" : "仅本次允许", exact: true }).click();
  if (config.mode === "cancel") {
    await page.waitForFunction(() => window.__appAcceptance.getState().messages.some(m => m.blocks.some(b =>
      b.kind === "tool" && b.name === "launch_desktop_application" && b.result?.includes('"accepted"'))), null, { timeout: 60000 });
    report.cancelOutcome = await page.evaluate(() => window.__appAcceptance.getState().stop());
  }
  await page.waitForFunction(id => Object.values(window.__appAcceptance.getState().runSnapshots)
    .some(r => r.session_id === id && r.status === "terminal"), report.sessionId, { timeout: 120000 });
  report.state = await page.evaluate(id => ({
    run: Object.values(window.__appAcceptance.getState().runSnapshots).find(r => r.session_id === id),
    messages: window.__appAcceptance.getState().messages.map(m => ({ role: m.role, status: m.status, blocks: m.blocks })),
    processes: Object.values(window.__appApprovals.getState().processes).filter(p => p.session_id === id),
  }), report.sessionId);
  report.backend = await (await fetch("http://127.0.0.1:18800/v1/runs/" + report.state.run.run_id)).json();
  await page.screenshot({ path: config.output + ".final.png" });
  report.status = "observed";
} catch (error) {
  report.error = String(error);
  if (page && report.sessionId) {
    try {
      report.failureCleanup = await page.evaluate(async id => {
        const s = window.__appAcceptance;
        if (s.getState().currentSessionId === id && Object.values(s.getState().runSnapshots)
          .some(r => r.session_id === id && r.status !== "terminal")) return await s.getState().stop();
        return "already_terminal";
      }, report.sessionId);
    } catch (cleanupError) { report.cleanupError = String(cleanupError); }
  }
  process.exitCode = 1;
} finally {
  await writeFile(config.output, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ output: config.output, status: report.status, error: report.error, sessionId: report.sessionId }));
  await browser.close();
}
