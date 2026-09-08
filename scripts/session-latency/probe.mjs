import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "frontend/package.json"));
const { chromium } = require("playwright");
const options = {};
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i];
  const value = process.argv[i + 1];
  if (!key?.startsWith("--") || value === undefined) throw new Error("Arguments require --key value");
  options[key.slice(2)] = value;
}
if (!options["title-a"] || !options["title-b"] || !options.output) {
  throw new Error("Required: --title-a TITLE --title-b TITLE --output FILE; optional --cdp URL --samples 8");
}
const samples = Number(options.samples ?? 8);
if (!Number.isInteger(samples) || samples < 2 || samples > 40) throw new Error("samples must be 2..40");
const browser = await chromium.connectOverCDP(options.cdp ?? "http://127.0.0.1:9222", { noDefaults: true });
try {
  const matches = browser.contexts().flatMap(c => c.pages()).filter(p => new URL(p.url()).pathname === "/chat.html");
  if (matches.length !== 1) throw new Error("Expected exactly one chat WebView");
  const page = matches[0];
  if (!await page.evaluate(() => Boolean(window.__TAURI_INTERNALS__))) throw new Error("A real Tauri WebView is required");
  const titles = [options["title-a"], options["title-b"]];
  await page.evaluate(async () => {
    window.__latencySessions = (await import("/src/stores/sessions.ts")).useSessionsStore;
  });
  await page.waitForFunction(names => {
    return names.every(name => window.__latencySessions.getState().list.filter(s => s.title === name).length === 1);
  }, titles, { timeout: 15000 });
  const targets = await page.evaluate(async names => {
    const { useSessionsStore } = await import("/src/stores/sessions.ts");
    return names.map(name => {
      const found = useSessionsStore.getState().list.filter(s => s.title === name);
      if (found.length !== 1) throw new Error("Each title must resolve to one session");
      return found[0].session_id;
    });
  }, titles);
  const requests = [];
  page.on("requestfinished", request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/v1/sessions/")) return;
    requests.push({
      route: url.pathname.replace(/\/sessions\/[^/]+/, "/sessions/<id>"),
      method: request.method(), timing: request.timing(),
    });
  });
  const rows = [];
  for (let i = 0; i < samples; i++) {
    const selected = await page.evaluate(async () => {
      const { useConversationStore } = await import("/src/stores/conversation.ts");
      return useConversationStore.getState().currentSessionId;
    });
    const target = targets[0] === selected ? 1 : 0;
    const cachedBefore = await page.evaluate(async sessionId => {
      const { useConversationStore } = await import("/src/stores/conversation.ts");
      return Boolean(useConversationStore.getState().sessionViews[sessionId]?.messages.length);
    }, targets[target]);
    await page.evaluate(async sessionId => {
      const { useConversationStore } = await import("/src/stores/conversation.ts");
      window.__sessionLatency = new Promise((resolve, reject) => {
        document.addEventListener("click", () => {
          const start = performance.now();
          const startedAt = Date.now();
          let firstContentMs = null;
          function sample() {
            const state = useConversationStore.getState();
            const nodes = [...document.querySelectorAll("[data-message-id]")];
            const ids = new Set(state.messages.map(m => m.id));
            const contentReady = state.currentSessionId === sessionId && nodes.length > 0
              && nodes.every(n => ids.has(n.getAttribute("data-message-id")));
            if (contentReady && firstContentMs === null) firstContentMs = performance.now() - start;
            if (contentReady && !state.historyLoading) {
              const viewport = document.querySelector(".chat-message-viewport");
              resolve({ startedAt, firstContentMs, readyMs: performance.now() - start,
                messages: state.messages.length, domMessages: nodes.length,
                viewport: viewport ? { clientHeight: viewport.clientHeight, scrollHeight: viewport.scrollHeight } : null });
            } else if (performance.now() - start > 30000) reject(new Error("Session did not become ready"));
            else requestAnimationFrame(sample);
          }
          requestAnimationFrame(sample);
        }, { once: true, capture: true });
      });
    }, targets[target]);
    await page.locator('[data-sidebar="menu-button"]').filter({ hasText: titles[target] }).click();
    const row = { sample: i + 1, target: target === 0 ? "A" : "B", cachedBefore, ...await page.evaluate(() => window.__sessionLatency) };
    rows.push(row);
    console.log(JSON.stringify(row));
  }
  const report = {
    schemaVersion: 1, collectedAt: new Date().toISOString(), surface: "Tauri WebView CDP",
    head: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
    overlay: execFileSync("git", ["status", "--porcelain"], { cwd: root, encoding: "utf8" }),
    rows, requests,
    limitations: "Existing profile and background workers; frame-sampled DOM readiness; no model calls. Runtime lease must be verified separately.",
  };
  await mkdir(dirname(resolve(options.output)), { recursive: true });
  await writeFile(resolve(options.output), JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
  console.log(`Report: ${resolve(options.output)}`);
} finally {
  await browser.close();
}
