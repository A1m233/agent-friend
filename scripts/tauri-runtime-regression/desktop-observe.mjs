// Windows installer-smoke WebView observation. No messages are submitted.
import { createRequire } from "node:module";
import { appendFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { chromium } = require("playwright");
const [portText, output, durationText, buildId] = process.argv.slice(2);
const port = Number(portText);
const duration = Number(durationText);
if (!Number.isInteger(port) || port < 1024 || port > 65535
    || !Number.isInteger(duration) || duration < 60 || duration > 3600 || !buildId) {
  throw new Error("invalid bounded desktop observer arguments");
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const report = { schemaVersion: 1, ok: false, surface: "desktop DOM via CDP",
  buildId, requestedSeconds: duration, ticks: 0, reloads: 0, nativeQueries: 0,
  passthroughRoundTrips: 0, cursorSamples: 0, errors: [], externalRequestsBlocked: 0,
  notVerified: ["physical mouse click-through to another native window",
    "physical mixed-DPI cross-monitor dragging", "user tray quit"] };
let browser;
let pet;
let chat;
let unlisten;
let cursorBefore = 0;
const timeout = (promise, ms = 10000) => {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("native operation timed out")), ms);
  })]).finally(() => clearTimeout(timer));
};
const native = (page, command, args = {}) => timeout(page.evaluate(({ command, args }) =>
  window.__TAURI_INTERNALS__.invoke(command, args), { command, args }));

async function requireIdentity(page, label) {
  await page.waitForFunction(() => Boolean(window.__TAURI_INTERNALS__?.invoke), null, { timeout: 15000 });
  const identity = await page.evaluate(() => ({ endpoints: window.__AGENT_FRIEND_LOCAL_SERVICES__,
    label: window.__TAURI_INTERNALS__.metadata.currentWindow.label }));
  if (identity.label !== label || identity.endpoints?.profileId !== "installer-smoke"
      || identity.endpoints.bridge !== "http://127.0.0.1:28800"
      || identity.endpoints.voice !== "http://127.0.0.1:28900") {
    throw new Error(`refusing non-isolated ${label} WebView`);
  }
  return identity;
}

async function armCursor() {
  await requireIdentity(pet, "pet");
  const ids = await pet.evaluate(async () => {
    const observation = { count: 0, latest: null };
    window.__afNativeRegressionCursor = observation;
    const handler = window.__TAURI_INTERNALS__.transformCallback((event) => {
      observation.count += 1;
      observation.latest = event.payload;
    });
    const eventId = await window.__TAURI_INTERNALS__.invoke("plugin:event|listen", {
      event: "pet://cursor", target: { kind: "AnyLabel", label: "pet" }, handler,
    });
    return { handler, eventId };
  });
  cursorBefore = 0;
  unlisten = async () => pet.evaluate(async ({ handler, eventId }) => {
    window.__TAURI_EVENT_PLUGIN_INTERNALS__.unregisterListener("pet://cursor", eventId);
    await window.__TAURI_INTERNALS__.invoke("plugin:event|unlisten", { event: "pet://cursor", eventId });
    window.__TAURI_INTERNALS__.unregisterCallback(handler);
  }, ids);
}

try {
  const startupDeadline = Date.now() + 120000;
  while (Date.now() < startupDeadline && !browser) {
    try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 1500 }); }
    catch { await wait(500); }
  }
  if (!browser) throw new Error("isolated WebView CDP unavailable");
  while (Date.now() < startupDeadline && (!pet || !chat)) {
    const pages = browser.contexts().flatMap((context) => context.pages());
    pet = pages.find((page) => /\/pet\.html(?:[?#]|$)/.test(page.url()));
    chat = pages.find((page) => /\/chat\.html(?:[?#]|$)/.test(page.url()));
    if (!pet || !chat) await wait(250);
  }
  if (!pet || !chat) throw new Error("isolated pet/chat WebViews missing");
  report.identity = await requireIdentity(chat, "chat");
  await requireIdentity(pet, "pet");
  // Browser networking outside local packaged assets/services is not part of this test.
  for (const context of browser.contexts()) {
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (/^https?:$/.test(url.protocol)
          && !["127.0.0.1", "localhost", "tauri.localhost", "asset.localhost", "ipc.localhost"].includes(url.hostname)) {
        report.externalRequestsBlocked += 1;
        await route.abort();
      } else { await route.continue(); }
    });
  }
  for (const page of [pet, chat]) {
    page.on("pageerror", (error) => {
      if (report.errors.length < 25) report.errors.push({ kind: "pageerror", message: error.message.slice(0, 300) });
    });
  }
  await native(chat, "open_chat");
  report.initialTopology = await native(pet, "get_pet_accessory_context");
  await armCursor();
  await wait(1000);
  const started = Date.now();
  report.startedAt = new Date(started).toISOString();
  let nextReload = started + 18000;
  let nextToggle = started;
  let lastPulse = 0;
  while (Date.now() - started < duration * 1000) {
    const tick = await timeout(Promise.all([
      native(pet, "get_pet_accessory_context"),
      native(chat, "plugin:window|available_monitors"),
      native(chat, "plugin:window|cursor_position"),
      native(chat, "get_runtime_service_snapshot", { service: "bridge" }),
      pet.evaluate(() => ({ cursor: window.__afNativeRegressionCursor,
        dpr: devicePixelRatio, viewport: { width: innerWidth, height: innerHeight },
        canvases: Array.from(document.querySelectorAll("canvas")).map((canvas) => ({ width: canvas.width, height: canvas.height })) })),
    ]));
    const [topology, monitors, cursorPosition, bridge, frontend] = tick;
    const delta = frontend.cursor.count - cursorBefore;
    cursorBefore = frontend.cursor.count;
    if (delta <= 0 || !frontend.cursor.latest || !Number.isFinite(frontend.cursor.latest.x)
        || !Number.isFinite(frontend.cursor.latest.y)) throw new Error("native cursor feed stopped or became invalid");
    if (!Array.isArray(monitors) || monitors.length === 0) throw new Error("native monitor query returned no displays");
    report.cursorSamples += delta;
    report.nativeQueries += 4;
    report.ticks += 1;
    if (Date.now() >= nextToggle) {
      try {
        await native(pet, "set_pet_cursor_passthrough", { ignore: false, diagnostic: null });
      } finally {
        await native(pet, "set_pet_cursor_passthrough", { ignore: true, diagnostic: null });
      }
      report.passthroughRoundTrips += 1;
      nextToggle = Date.now() + 30000;
    }
    if (Date.now() - lastPulse >= 30000) {
      await appendFile(join(output, "desktop-samples.jsonl"), `${JSON.stringify({ at: new Date().toISOString(),
        elapsedSeconds: (Date.now() - started) / 1000, topology, monitors, cursorPosition,
        cursor: frontend.cursor.latest, dpr: frontend.dpr, viewport: frontend.viewport,
        canvases: frontend.canvases, bridge, samples: report.cursorSamples })}\n`);
      console.log(`desktop stress ${Math.round((Date.now() - started) / 1000)}s / ${duration}s; cursor=${report.cursorSamples}; reloads=${report.reloads}`);
      lastPulse = Date.now();
    }
    if (Date.now() >= nextReload) {
      const target = report.reloads % 2 === 0 ? pet : chat;
      if (target === pet) { await unlisten(); unlisten = null; }
      await target.reload({ waitUntil: "domcontentloaded", timeout: 15000 });
      await requireIdentity(target, target === pet ? "pet" : "chat");
      if (target === pet) await armCursor();
      report.reloads += 1;
      nextReload += 18000;
    }
    await wait(1000);
  }
  report.elapsedSeconds = (Date.now() - started) / 1000;
  report.finalTopology = await native(pet, "get_pet_accessory_context");
  await chat.screenshot({ path: join(output, "chat-webview.png") });
  report.screenshot = "chat-webview.png (isolated chat WebView only; no desktop capture)";
  report.ok = report.elapsedSeconds >= duration && report.cursorSamples > duration * 5;
} catch (error) {
  report.error = String(error);
  throw error;
} finally {
  if (unlisten) await unlisten().catch(() => {});
  if (pet) await native(pet, "set_pet_cursor_passthrough", { ignore: true, diagnostic: null }).catch(() => {});
  if (browser) await browser.close();
  await writeFile(join(output, "desktop-observation.json"), `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
}
