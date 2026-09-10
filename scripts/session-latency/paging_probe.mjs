/** Read-only desktop pagination acceptance; never submits a model request. */
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile, access } from "node:fs/promises";

const require = createRequire(resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/package.json"));
const { chromium } = require("playwright");
const options = {};
for (let i = 2; i < process.argv.length; i += 2) {
  if (!process.argv[i]?.startsWith("--") || process.argv[i + 1] === undefined) throw new Error("Expected --key value");
  options[process.argv[i].slice(2)] = process.argv[i + 1];
}
if (!options.title || !options.output) throw new Error("Required --title TITLE --output PRIVATE_FILE; optional --cdp URL");
try { await access(options.output); throw new Error("Output already exists"); }
catch (error) { if (error.code !== "ENOENT") throw error; }
const browser = await chromium.connectOverCDP(options.cdp ?? "http://127.0.0.1:9222", { noDefaults: true });
try {
  const pages = browser.contexts().flatMap(c => c.pages()).filter(p => new URL(p.url()).pathname === "/chat.html");
  if (pages.length !== 1) throw new Error("Expected one real chat WebView");
  const page = pages[0];
  if (!await page.evaluate(() => Boolean(window.__TAURI_INTERNALS__))) throw new Error("Tauri required");
  await page.evaluate(async () => { window.__pagingStore = (await import("/src/stores/conversation.ts")).useConversationStore; });
  const started = performance.now();
  await page.locator('[data-sidebar="menu-button"]').filter({ hasText: options.title }).click();
  await page.waitForFunction(() => !window.__pagingStore.getState().historyLoading);
  const initial = await page.evaluate(() => ({ messages: window.__pagingStore.getState().messages.length,
    older: window.__pagingStore.getState().hasOlderHistory() }));
  if (!initial.older) throw new Error("Choose a session with more than 30 complete turns");
  const openMs = performance.now() - started;
  const viewport = page.locator(".chat-message-viewport");
  const box = await viewport.boundingBox();
  if (!box) throw new Error("Chat viewport unavailable");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -350);
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    const viewport = document.querySelector(".chat-message-viewport");
    const top = viewport.getBoundingClientRect().top;
    const anchor = [...viewport.querySelectorAll("[data-message-id]")].find(node => node.getBoundingClientRect().bottom > top);
    if (!anchor) throw new Error("No visible anchor");
    const id = anchor.getAttribute("data-message-id");
    const baseline = anchor.getBoundingClientRect().top;
    const start = performance.now();
    window.__pagingFrames = new Promise(resolve => {
      const rows = [];
      const sample = () => {
        const node = [...viewport.querySelectorAll("[data-message-id]")].find(el => el.getAttribute("data-message-id") === id);
        rows.push({ ms: performance.now() - start, anchorDisplacement: node ? node.getBoundingClientRect().top - baseline : null,
          messages: window.__pagingStore.getState().messages.length });
        if (performance.now() - start < 2500) requestAnimationFrame(sample);
        else resolve(rows);
      };
      requestAnimationFrame(sample);
    });
  });
  await page.getByRole("button", { name: "加载更早的消息", exact: true }).click();
  await page.waitForFunction(count => window.__pagingStore.getState().messages.length > count, initial.messages);
  const frames = await page.evaluate(() => window.__pagingFrames);
  const final = await page.evaluate(() => ({ messages: window.__pagingStore.getState().messages.length,
    older: window.__pagingStore.getState().hasOlderHistory() }));
  const report = { kind: "desktop DOM via CDP", openMs, initial, final, frames,
    limits: "Read-only opening and one prepend; no send, provider, or persistence-write acceptance. Runtime identity must be verified with dev status before running." };
  await writeFile(options.output, JSON.stringify(report, null, 2), { flag: "wx" });
  console.log(JSON.stringify({ openMs, initial, final,
    maxAnchorDisplacement: Math.max(...frames.map(row => Math.abs(row.anchorDisplacement ?? 0))) }));
} finally { await browser.close(); }
