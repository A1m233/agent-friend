// Explicitly authorized real-model acceptance only; never retries a send.
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile } from "node:fs/promises";
const require = createRequire(resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/package.json"));
const { chromium } = require("playwright");
const [sessionId, output, mode = "cancel", backgroundAuthorization] = process.argv.slice(2);
if (backgroundAuthorization !== "--background-calls-authorized") {
  throw new Error("Real sends/cancels can trigger memory extraction and notifications. Explicit authorization including those calls is required; run.model_calls is not a total API budget counter.");
}
if (!["cancel", "continue"].includes(mode)) throw new Error("Mode must be cancel or continue");
if (!sessionId || !output) throw new Error("Requires authorized session ID and private output path");
await writeFile(output, JSON.stringify({ status: "reserved", sessionId }), { flag: "wx" });
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
const report = { sessionId, started: new Date().toISOString() };
try {
  const page = browser.contexts().flatMap(c => c.pages()).find(p => new URL(p.url()).pathname === "/chat.html");
  page.setDefaultTimeout(10000);
  const initial = await page.evaluate(async () => {
    const s = (await import("/src/stores/conversation.ts")).useConversationStore.getState();
    return { id: s.currentSessionId, model: s.currentModel, reasoning: s.currentReasoningPreset,
      active: Object.values(s.runSnapshots).some(r => r.status !== "terminal") };
  });
  if (initial.id !== sessionId || initial.model !== "deepseek/deepseek-v4-flash"
    || initial.reasoning !== "off" || initial.active) throw new Error("Unexpected test session state");
  await page.evaluate(async () => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    window.__realStore = useConversationStore;
    window.__realFrames = [];
    window.__realUnsub = useConversationStore.subscribe(s => {
      window.__realFrames.push({ ms: performance.now(), id: s.currentSessionId,
        runs: Object.values(s.runSnapshots).map(r => ({ id: r.run_id, status: r.status,
          outcome: r.outcome, input: r.input_accepted, calls: r.model_calls })),
        messages: s.messages.map(m => ({ role: m.role, status: m.status,
          chars: m.blocks.filter(b => b.kind === "text").reduce((n, b) => n + b.text.length, 0) })) });
    });
  });
  try {
    await page.locator("textarea").fill(mode === "continue"
      ? "停止之前的数字输出。现在验证取消后续聊：请只调用一次 calculate 工具计算17*23，不要调用其它工具，不要修改文件。得到工具结果后用一句话报告结果并结束。"
      : "继续进行需求120的独立取消测试。不要调用工具，不要修改文件。请按顺序逐行输出数字1到200，每行一个数字，不要省略。我会中途取消。");
    report.submitAt = Date.now();
    await page.locator("textarea").press("Enter");
    if (mode === "continue") {
      await page.waitForFunction(() => Object.values(window.__realStore.getState().runSnapshots)
        .some(r => r.status !== "terminal"), null, { timeout: 10000 });
      report.running = await page.evaluate(() => Object.values(window.__realStore.getState().runSnapshots)
        .find(r => r.status !== "terminal"));
      await page.waitForFunction(id => window.__realStore.getState().runSnapshots[id]?.status === "terminal",
        report.running.run_id, { timeout: 55000 });
      report.doneAt = Date.now();
      report.backend = await page.evaluate(async id => await (await fetch("/v1/runs/" + id)).json(), report.running.run_id);
      report.finalMessages = await page.evaluate(() => window.__realStore.getState().messages
        .map(m => ({ role: m.role, status: m.status, runId: m.runId,
          blocks: m.blocks.map(b => b.kind === "tool" ? { kind: b.kind, name: b.name, status: b.status, result: b.result }
            : b.kind === "text" ? { kind: b.kind, text: b.text } : { kind: b.kind }) })));
    } else {
    await page.waitForFunction(() => {
      const s = window.__realStore.getState();
      return s.messages.some(m => m.role === "assistant" && m.status === "streaming"
        && m.blocks.some(b => b.kind === "text" && /1\s+2\s+3/.test(b.text)));
    }, null, { timeout: 45000 });
    report.firstOutputAt = Date.now();
    report.running = await page.evaluate(async () => Object.values(
      (await import("/src/stores/conversation.ts")).useConversationStore.getState().runSnapshots,
    ).find(r => r.status !== "terminal"));
    report.shortClickAt = Date.now();
    await page.locator('[data-sidebar="menu-button"]').filter({ hasText: "会话 2026-09-05 22:46" }).click();
    await page.waitForFunction(() => {
      const s = window.__realStore.getState();
      return s.currentSessionId === "16ef8d12-fb8a-4826-89e0-898931dbe4d5"
        && !s.historyLoading && s.messages.length > 0;
    });
    report.shortReadyAt = Date.now();
    const listing = await page.evaluate(async () => await (await fetch("/v1/sessions")).json());
    const entries = Array.isArray(listing) ? listing : listing.sessions;
    const row = entries.find(x => (x.id ?? x.session_id) === sessionId);
    if (!row?.title) throw new Error("Test session title unavailable");
    await page.locator('[data-sidebar="menu-button"]').filter({ hasText: row.title }).click();
    await page.waitForFunction(id => window.__realStore.getState().currentSessionId === id, sessionId);
    report.cancelAt = Date.now();
    await page.locator("t-chat-sender button").last().click();
    await page.waitForFunction(id => window.__realStore.getState().runSnapshots[id]?.status === "terminal",
    report.running.run_id, { timeout: 30000 });
    report.doneAt = Date.now();
    report.backend = await page.evaluate(async id => await (await fetch("/v1/runs/" + id)).json(), report.running.run_id);
    }
  } finally {
    report.frames = await page.evaluate(() => { window.__realUnsub?.(); return window.__realFrames; });
  }
} catch (error) {
  report.error = String(error);
  throw error;
} finally {
  await writeFile(output, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, frames: report.frames?.length }));
  await browser.close();
}
