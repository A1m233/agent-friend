import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile } from "node:fs/promises";
const require = createRequire(resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/package.json"));
const { chromium } = require("playwright");
const output = process.argv[2];
if (!output) throw new Error("scroll requires a private output filename");
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
try {
  const page = browser.contexts().flatMap(c => c.pages()).find(p => new URL(p.url()).pathname === "/chat.html");
  const runtime = await page.evaluate(async () => {
    const entry = performance.getEntriesByType("resource")
      .filter(e => e.name.includes("@tanstack_react-virtual.js")).at(-1);
    if (!entry) throw new Error("Loaded virtualizer module URL is unavailable");
    const source = await (await fetch(entry.name, { cache: "no-store" })).text();
    return { moduleUrl: entry.name, coreVersion: source.match(/virtual-core@([^/]+)/)?.[1],
      synchronousResizeFix: source.includes("notify(adjustedSync)") };
  });
  await page.locator('[data-sidebar="menu-button"]').filter({ hasText: "会话 2026-09-05 22:46" }).click();
  await page.evaluate(async () => {
    window.__scrollStore = (await import("/src/stores/conversation.ts")).useConversationStore;
  });
  await page.waitForFunction(() => !window.__scrollStore.getState().historyLoading);
  await page.evaluate(async () => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const rows = [], calls = [], wheels = [];
    const started = performance.now();
    const original = Element.prototype.scrollTo;
    Element.prototype.scrollTo = function(...args) {
      if (this.matches(".chat-message-viewport")) calls.push({ ms: performance.now() - started, args, stack: new Error().stack });
      return original.apply(this, args);
    };
    const onWheel = event => wheels.push({ ms: performance.now() - started, deltaY: event.deltaY });
    document.addEventListener("wheel", onWheel, true);
    window.__scrollProbe = new Promise(resolve => {
      const sample = () => {
        const viewport = document.querySelector(".chat-message-viewport");
        const s = useConversationStore.getState();
        if (viewport) {
          const bounds = viewport.getBoundingClientRect();
          const first = [...viewport.querySelectorAll("[data-message-id]")].find(e => e.getBoundingClientRect().bottom > bounds.top);
          rows.push({ ms: performance.now() - started, top: viewport.scrollTop, height: viewport.scrollHeight,
            loading: s.historyLoading, seq: s.historyLoadSeq, messages: s.messages.length,
            anchor: first?.getAttribute("data-message-id"), anchorTop: first ? first.getBoundingClientRect().top - bounds.top : null });
        }
        if (performance.now() - started < 6500) requestAnimationFrame(sample);
        else {
          Element.prototype.scrollTo = original;
          document.removeEventListener("wheel", onWheel, true);
          resolve({ rows, calls, wheels });
        }
      };
      requestAnimationFrame(sample);
    });
  });
  await page.locator('[data-sidebar="menu-button"]').filter({ hasText: "长会话" }).click();
  await page.locator(".chat-message-viewport").waitFor();
  const box = await page.locator(".chat-message-viewport").boundingBox();
  await page.mouse.move(box.x + 100, box.y + 120);
  for (let index = 0; index < 15; index++) {
    await page.mouse.wheel(0, -280);
    await page.waitForTimeout(120);
  }
  const report = await page.evaluate(() => window.__scrollProbe);
  report.runtime = runtime;
  // Positive scrollTop changes can be correct height compensation. Count only the
  // observed stale-layout signature: wrong anchor, then the original returns.
  report.geometryFlashes = report.rows.flatMap((row, index, rows) => {
    const previous = rows[index - 1];
    if (!previous || row.ms <= report.wheels[0]?.ms || row.top - previous.top <= 150
      || row.height !== previous.height || row.anchor === previous.anchor) return [];
    const recovered = rows.slice(index + 1).find(next => next.ms - row.ms < 50
      && next.anchor === previous.anchor && Math.abs(next.anchorTop - previous.anchorTop) < 1);
    return recovered ? [{ ms: row.ms, delta: row.top - previous.top,
      durationMs: recovered.ms - row.ms }] : [];
  });
  await writeFile(output, JSON.stringify(report, null, 2), { flag: "wx" });
  console.log(JSON.stringify({ frames: report.rows.length, calls: report.calls.length,
    runtime, geometryFlashes: report.geometryFlashes }));
} finally { await browser.close(); }
