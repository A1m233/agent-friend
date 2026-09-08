// Explicit desktop configuration probe: restores the selected session's original settings.
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile } from "node:fs/promises";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "frontend/package.json"));
const { chromium } = require("playwright");
const output = process.argv[2];
if (!output) throw new Error("config requires an output filename; selected session must use kimi-k3/max");
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
try {
  const page = browser.contexts().flatMap(c => c.pages()).find(p => new URL(p.url()).pathname === "/chat.html");
  await page.evaluate(async () => {
    window.__configStore = (await import("/src/stores/conversation.ts")).useConversationStore;
  });
  const snapshot = () => page.evaluate(async () => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const s = useConversationStore.getState();
    return { model: s.currentModel, reasoning: s.currentReasoningPreset, switching: s.modelSwitching,
      id: s.currentSessionId, history: s.historyLoading, tauri: Boolean(window.__TAURI_INTERNALS__) };
  });
  const original = await snapshot();
  if (!original.tauri || !original.id || original.history || original.switching
    || original.model !== "kimi/kimi-k3" || original.reasoning !== "max") throw new Error("Unexpected initial state");
  const rows = [];
  const requests = [];
  const pending = [];
  page.on("requestfinished", request => {
    if (!request.url().includes("/runtime-config")) return;
    pending.push((async () => {
      const response = await request.response();
      requests.push({ timing: request.timing(), status: response.status(),
        requestId: await response.headerValue("x-agent-friend-request-id") });
    })());
  });
  const change = async (kind, label, model, reasoning) => {
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /当前模型/ }).click();
    await page.getByRole("menuitem", { name: kind === "model" ? /^模型/ : /^推理强度/ }).hover();
    await page.evaluate(() => {
      document.addEventListener("click", () => { window.__configClick = performance.now(); }, { once: true, capture: true });
    });
    await page.getByRole(kind === "model" ? "menuitem" : "menuitemradio", { name: label, exact: true }).click();
    await page.waitForFunction(target => {
      const s = window.__configStore.getState();
      return !s.modelSwitching && s.currentModel === target.model && s.currentReasoningPreset === target.reasoning;
    }, { model, reasoning }, { timeout: 30000 });
    const row = await page.evaluate(() => ({ readyUpperMs: performance.now() - window.__configClick }));
    const current = await snapshot();
    if (current.id !== original.id) throw new Error("Session changed");
    rows.push({ kind, model, reasoning, ...row });
    console.log(JSON.stringify(rows.at(-1)));
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
  };
  try {
    await change("reasoning", "高", "kimi/kimi-k3", "high");
    await change("reasoning", "最高", "kimi/kimi-k3", "max");
    await change("model", "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-flash", "off");
  } finally {
    const current = await snapshot();
    if (current.model !== original.model) {
      await page.keyboard.press("Escape");
      await page.keyboard.press("Escape");
      // Default K3 reasoning is read from the UI after the model command settles.
      await page.getByRole("button", { name: /当前模型/ }).click();
      await page.getByRole("menuitem", { name: /^模型/ }).hover();
      await page.getByRole("menuitem", { name: "kimi-k3", exact: true }).click();
      await page.waitForFunction(() => {
        const s = window.__configStore.getState(); return s.currentModel === "kimi/kimi-k3" && !s.modelSwitching;
      }, null, { timeout: 30000 });
    }
    if ((await snapshot()).reasoning !== original.reasoning) await change("reasoning", "最高", "kimi/kimi-k3", "max");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
  }
  await Promise.all(pending);
  await writeFile(output, JSON.stringify({ rows, requests, restored: (await snapshot()).reasoning === "max",
    limits: "Real Tauri clicks, DOM/store completion upper bound including observer overhead; no LLM calls." }, null, 2), { flag: "wx" });
} finally { await browser.close(); }
