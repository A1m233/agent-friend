#!/usr/bin/env node
// Attaches to an existing Tauri app; only explicit restart-bridge restarts its supervised bridge.
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "frontend/package.json"));
const [command = "inspect", ...args] = process.argv.slice(2);
const traceRepeat = Number(args.find((arg) => arg.startsWith("--repeat="))?.slice(9) ?? 1);
const traceDuration = Number(args.find((arg) => arg.startsWith("--duration="))?.slice(11) ?? 1500);
if (!Number.isInteger(traceRepeat) || traceRepeat < 1 || traceRepeat > 4) throw new Error("--repeat must be 1 through 4");
if (!Number.isInteger(traceDuration) || traceDuration < 500 || traceDuration > 5000) throw new Error("--duration must be 500 through 5000 ms");
const commands = ["inspect", "hover", "away", "wheel", "tooltip", "action", "button", "drag", "close", "trace-close", "expand", "settings", "reload-pet", "reload-bubble", "reload-chat", "restart-bridge"];
if (command === "--help" || command === "help") {
  console.log("Attach to an authorized, identity-verified Tauri runtime on CDP 9222. Never starts/stops the app or calls an LLM.\nCommands: inspect | hover | tooltip 'button label' | action short/long/stack/clear | drag X Y | close ID | trace-close ID | expand ID | settings inspect/off/on | reload-pet | reload-bubble | reload-chat\ntrace-close: real close click, then 1.5 seconds of rAF DOM/native geometry tracing; JSON excludes notification text. Final screenshot may show text.\nPet buttons: button '打开设置' / '缩小桌宠并移动到边上' / '恢复桌宠大小'\nOptional: --output=<private-directory> for timestamped JSON and bubble screenshots.");
  console.log("trace-close options: --repeat=1..4 (rapid focused Enter closes), --duration=500..5000 (milliseconds after click). Notification HTTP metadata excludes bodies and headers.");
  console.log("ActionBar paging: wheel DELTA_Y [DELTA_X] [--count=1..10] uses real wheel events; away leaves the pet to test hover remount. Reports include live and persisted page indices.");
  console.log("restart-bridge: explicitly reload changed Python code through the existing runtime supervisor, honoring its active-work guard. Keeps the desktop app open; no screenshot or test notification is produced.");
  process.exit(0);
}
if (!commands.includes(command)) throw new Error(`Unknown command: ${command}; use --help`);
const { chromium } = require("playwright");
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222");
const pages = browser.contexts().flatMap((context) => context.pages());
const pet = pages.find((page) => new URL(page.url()).pathname.endsWith("/pet.html"));
const bubble = pages.find((page) => new URL(page.url()).pathname.endsWith("/bubble.html"));
const chat = pages.find((page) => new URL(page.url()).pathname.endsWith("/chat.html"));
if (!pet || !bubble) throw new Error("Expected real pet.html and bubble.html WebViews");
if (command === "restart-bridge") {
  try {
    await pet.evaluate(async () => {
      const { restartLocalService } = await import("/src/services/runtime.ts");
      await restartLocalService("bridge", "controlled_restart");
    });
    console.log(JSON.stringify({ command, restartedAt: new Date().toISOString(), page: pet.url() }));
  } finally {
    await browser.close();
  }
  process.exit(0);
}
const native = (page, command, args) => page.evaluate(async ({ command, args }) => {
  const { invoke } = await import("/node_modules/@tauri-apps/api/core.js");
  return invoke(command, args);
}, { command, args });
const anchor = () => pet.evaluate(() => window.__AGENT_FRIEND_ANCHOR_DIAGNOSTICS__);
async function move(page, x, y) {
  if (process.platform === "win32") {
    const geometry = await page.evaluate(async () => {
      const { getCurrentWindow } = await import("/node_modules/@tauri-apps/api/window.js");
      return { origin: await getCurrentWindow().outerPosition(), dpr: devicePixelRatio };
    });
    const result = spawnSync("uv", ["run", "python", "scripts/notification-visual/cursor.py",
      String(Math.round(geometry.origin.x + x * geometry.dpr)),
      String(Math.round(geometry.origin.y + y * geometry.dpr))], { cwd: root, encoding: "utf8" });
    if (result.status !== 0) throw new Error(result.stderr);
  }
  await page.mouse.move(x, y);
  await page.waitForTimeout(150);
}
async function hoverPet() {
  const info = await anchor();
  const rect = info?.visibleBounds ?? info?.spriteScreen;
  if (!rect) throw new Error("Avatar geometry not ready");
  await move(pet, rect.x + rect.w / 2, rect.y + rect.h / 2);
  await pet.locator('[data-anchor-diagnostics="actionbar"]').waitFor({ state: "visible", timeout: 5000 });
}
async function click(page, locator, beforeClick) {
  const rect = await locator.boundingBox();
  if (!rect) throw new Error("Control is not rendered");
  const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  await move(page, x, y);
  const hit = await locator.evaluate((element, { x, y }) => {
    const hit = document.elementFromPoint(x, y);
    return hit === element || element.contains(hit);
  }, { x, y });
  if (!hit) throw new Error("Control is covered at its click point");
  if (beforeClick) await beforeClick();
  await page.mouse.click(x, y);
}

/** The installed page probe only reads geometry. Native reads are event-driven, never per frame. */
async function beginCloseTrace(notificationId) {
  await bubble.evaluate(async (targetId) => {
    const key = "__AGENT_FRIEND_NOTIFICATION_CLOSE_TRACE__";
    if (window[key]) throw new Error("A close trace is already active");
    const [{ getCurrentWindow }, { listen }] = await Promise.all([
      import("/node_modules/@tauri-apps/api/window.js"),
      import("/node_modules/@tauri-apps/api/event.js"),
    ]);
    const win = getCurrentWindow();
    const started = performance.now();
    const frames = [], events = [], errors = [], unlisteners = [];
    let nativeWindow = null, layout = null, eventVersion = 0;
    let frameHandle = 0, checkTimer = 0, watchdog = 0, active = true, finishing = false, checking = null, eventChecks = 0;
    let droppedFrames = 0, droppedEvents = 0, clickAtMs = null;
    const elementIds = new WeakMap();
    let elementSequence = 0;
    const round = (value) => Math.round(value * 1000) / 1000;
    const now = () => round(performance.now() - started);
    const rect = (value) => ({ x: round(value.x), y: round(value.y), w: round(value.width), h: round(value.height) });
    const point = (value) => ({ x: value.x, y: value.y });
    const size = (value) => ({ width: value.width, height: value.height });
    const appendEvent = (event) => { if (events.length < 300) events.push({ atMs: now(), ...event }); else droppedEvents++; };
    const failure = (operation, error) => errors.push({ atMs: now(), operation, type: error?.name ?? "Error" });
    const globalRect = (bounds, dpr) => nativeWindow ? {
      x: round(nativeWindow.innerPosition.x + bounds.x * dpr),
      y: round(nativeWindow.innerPosition.y + bounds.y * dpr),
      w: round(bounds.w * dpr), h: round(bounds.h * dpr),
    } : null;
    const scroll = (element) => ({ top: element.scrollTop, left: element.scrollLeft,
      height: element.scrollHeight, width: element.scrollWidth, clientHeight: element.clientHeight, clientWidth: element.clientWidth });
    const css = (element) => {
      const style = getComputedStyle(element);
      return { transform: style.transform, translate: style.translate, top: style.top, bottom: style.bottom,
        position: style.position, opacity: style.opacity, display: style.display, visibility: style.visibility,
        overflowX: style.overflowX, overflowY: style.overflowY,
        transitionProperty: style.transitionProperty, transitionDuration: style.transitionDuration,
        animationName: style.animationName, animationDuration: style.animationDuration,
        offset: style.getPropertyValue("--offset"), index: style.getPropertyValue("--index") };
    };
    const clippingAncestors = (element) => {
      const ancestors = [];
      for (let parent = element.parentElement; parent; parent = parent.parentElement) ancestors.push(parent);
      let start = 0;
      if (getComputedStyle(element).position === "fixed") {
        // A viewport-fixed exit snapshot escapes ordinary overflow ancestors.
        start = ancestors.findIndex((parent) => {
          const style = getComputedStyle(parent);
          return [style.transform, style.perspective, style.filter, style.backdropFilter].some((value) => value && value !== "none")
            || /paint|layout|strict|content/.test(style.contain) || /transform|perspective|filter/.test(style.willChange);
        });
        if (start < 0) return [];
      }
      return ancestors.slice(start).flatMap((parent) => {
        const style = getComputedStyle(parent);
        const clipX = /^(auto|scroll|hidden|clip)$/.test(style.overflowX);
        const clipY = /^(auto|scroll|hidden|clip)$/.test(style.overflowY);
        if (!clipX && !clipY) return [];
        return [{ bounds: rect(parent.getBoundingClientRect()), clipX, clipY,
          overflowX: style.overflowX, overflowY: style.overflowY,
          kind: parent.hasAttribute("data-bubble-viewport") ? "bubble-viewport"
            : parent.hasAttribute("data-bubble-stack") ? "bubble-stack"
              : parent.hasAttribute("data-sonner-toaster") ? "sonner-toaster" : parent.tagName.toLowerCase() }];
      });
    };
    const capture = (reason) => {
      if (!active) return;
      if (frames.length >= 2000) { droppedFrames++; return; }
      const dpr = devicePixelRatio;
      const viewportPhysical = { width: innerWidth * dpr, height: innerHeight * dpr };
      // WebView CSS viewport dimensions can round by a physical pixel at mixed DPR.
      // Larger mismatches identify mixed-time DOM/new-size + cached-old-origin samples.
      const roundingTolerancePhysical = Math.max(1, Math.ceil(dpr));
      const geometryConsistency = !nativeWindow ? { status: "unavailable", reason: "no-native-origin" }
        : Math.abs(viewportPhysical.width - nativeWindow.innerSize.width) > roundingTolerancePhysical
          || Math.abs(viewportPhysical.height - nativeWindow.innerSize.height) > roundingTolerancePhysical
          ? { status: "uncertain", reason: "viewport-native-size-mismatch" }
          : { status: "compatible", reason: "cached-native-size-compatible-with-dom" };
      const viewports = [...document.querySelectorAll("[data-sonner-toaster],[data-bubble-viewport],[data-bubble-stack]")]
        .map((element) => ({ bounds: rect(element.getBoundingClientRect()), scroll: scroll(element), css: css(element) }));
      const items = [...document.querySelectorAll("[data-sonner-toast],[data-bubble-item]")].map((element, index) => {
        if (!elementIds.has(element)) elementIds.set(element, ++elementSequence);
        const id = (element.getAttribute("data-notification-id")
          ?? element.querySelector("[data-notification-id]")?.getAttribute("data-notification-id")
          ?? element.getAttribute("data-bubble-item")) || null;
        const bounds = rect(element.getBoundingClientRect());
        const style = css(element);
        const state = Object.fromEntries(["visible", "removed", "mounted", "state", "status", "exiting", "exit", "expanded", "front", "index"]
          .map((name) => [name, element.getAttribute(`data-${name}`)]));
        const clips = clippingAncestors(element);
        const inViewport = bounds.w > 0 && bounds.h > 0 && bounds.x < innerWidth && bounds.y < innerHeight
          && bounds.x + bounds.w > 0 && bounds.y + bounds.h > 0
          && clips.every(({ bounds: clip, clipX, clipY }) =>
            (!clipX || bounds.x < clip.x + clip.w && bounds.x + bounds.w > clip.x)
            && (!clipY || bounds.y < clip.y + clip.h && bounds.y + bounds.h > clip.y));
        return { id, element: elementIds.get(element), domIndex: index, bounds, globalBoundsPhysical: globalRect(bounds, dpr),
          globalBoundsConfidence: geometryConsistency.status, clippingAncestors: clips,
          state, css: style, scroll: scroll(element), inViewport,
          painted: inViewport && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0,
          closeBounds: element.querySelector("[data-close-button]") ? rect(element.querySelector("[data-close-button]").getBoundingClientRect()) : null };
      });
      frames.push({ atMs: now(), reason, dpr, nativeWindow: nativeWindow ? structuredClone(nativeWindow) : null,
        geometryConsistency: { ...geometryConsistency, viewportPhysical, roundingTolerancePhysical },
        layout, viewport: { width: innerWidth, height: innerHeight, scrollX, scrollY,
          visual: visualViewport ? { x: visualViewport.offsetLeft, y: visualViewport.offsetTop, width: visualViewport.width, height: visualViewport.height, scale: visualViewport.scale } : null },
        viewports, items });
    };
    const readGeometry = async (reason) => {
      const revision = eventVersion;
      const requestedAtMs = now();
      try {
        const [innerPosition, outerPosition, innerSize, outerSize] = await Promise.all([
          win.innerPosition(), win.outerPosition(), win.innerSize(), win.outerSize(),
        ]);
        const observed = { innerPosition: point(innerPosition), outerPosition: point(outerPosition),
          innerSize: size(innerSize), outerSize: size(outerSize), source: reason, verifiedAtMs: now(), eventVersion: revision };
        const superseded = revision !== eventVersion;
        if (!superseded) nativeWindow = observed;
        appendEvent({ kind: "native-checkpoint", reason, requestedAtMs, superseded, observed });
        capture(`native-checkpoint:${reason}`);
      } catch (error) { failure("native-geometry", error); }
    };
    const scheduleGeometryCheck = () => {
      if (!active || finishing || checkTimer || checking || eventChecks >= 4) return;
      checkTimer = setTimeout(() => {
        checkTimer = 0;
        eventChecks++;
        checking = readGeometry("coalesced-window-event").finally(() => { checking = null; });
      }, 75);
    };
    const onNativeEvent = (kind, payload) => {
      eventVersion++;
      if (nativeWindow) {
        if (kind === "moved") {
          const dx = payload.x - nativeWindow.outerPosition.x, dy = payload.y - nativeWindow.outerPosition.y;
          nativeWindow = { ...nativeWindow, outerPosition: payload,
            innerPosition: { x: nativeWindow.innerPosition.x + dx, y: nativeWindow.innerPosition.y + dy } };
        } else if (kind === "resized") nativeWindow = { ...nativeWindow, innerSize: payload };
        nativeWindow = { ...nativeWindow, source: `${kind}-event`, eventVersion };
      }
      appendEvent({ kind, payload });
      capture(`native:${kind}`);
      scheduleGeometryCheck();
    };
    const stop = () => {
      active = false;
      cancelAnimationFrame(frameHandle);
      clearTimeout(checkTimer);
      clearTimeout(watchdog);
      for (const unlisten of unlisteners) unlisten();
      delete window[key];
    };
    window[key] = {
      markClick() { clickAtMs = now(); appendEvent({ kind: "before-click" }); capture("before-click"); },
      async finish() {
        finishing = true;
        clearTimeout(checkTimer);
        if (checking) await checking;
        await readGeometry("final");
        capture("final");
        const result = { targetId, startedAt: new Date(performance.timeOrigin + started).toISOString(),
          durationMs: now(), clickAtMs, requestedPostClickMs: 1500, units: { bounds: "CSS pixels in bubble WebView", globalBoundsPhysical: "physical desktop pixels using cached client origin" },
          nativeCheckLimit: 4, eventChecks, droppedFrames, droppedEvents, frames, events, errors };
        stop();
        return result;
      },
      abort: stop,
    };
    // Bound the probe even if its controlling CDP client disappears mid-command.
    watchdog = setTimeout(stop, 10000);
    try {
      unlisteners.push(await win.onMoved(({ payload }) => onNativeEvent("moved", point(payload))));
      unlisteners.push(await win.onResized(({ payload }) => onNativeEvent("resized", size(payload))));
      unlisteners.push(await listen("bubble://layout-applied", ({ payload }) => {
        layout = Object.fromEntries(["stateRevision", "layoutOwner", "visible", "applied", "measurementRevision", "layoutRevision", "widthCss", "heightCss",
          "hostRectPhysical", "targetRectPhysical", "scaleFactor", "retaining", "carrierPlan"]
          .map((field) => [field, payload[field]]));
        onNativeEvent("layout-applied", layout);
      }));
      await readGeometry("before");
      if (!nativeWindow) await readGeometry("before-retry");
      if (!nativeWindow) throw new Error("Cannot establish physical client origin for close trace");
      capture("before-pointer-move");
      const sample = () => {
        if (!active) return;
        capture("raf");
        frameHandle = requestAnimationFrame(sample);
      };
      frameHandle = requestAnimationFrame(sample);
    } catch (error) { stop(); throw error; }
  }, notificationId);
}

function summarizeCloseTrace(trace) {
  const baseline = trace.frames.find((frame) => frame.reason === "before-click") ?? trace.frames[0];
  const final = trace.frames.at(-1);
  const initialIds = baseline.items.map((item) => item.id).filter(Boolean);
  const initialPaintedIds = baseline.items.filter((item) => item.painted).map((item) => item.id).filter(Boolean);
  const changes = [];
  let lastOrder = "";
  for (const frame of trace.frames) {
    const state = frame.items.map((item) => ({ id: item.id, painted: item.painted,
      removed: item.state.removed, exiting: item.state.exiting, state: item.state.state }));
    const order = JSON.stringify(state);
    if (order !== lastOrder) { changes.push({ atMs: frame.atMs, items: state }); lastOrder = order; }
  }
  const finalIds = final.items.map((item) => item.id).filter(Boolean);
  const survivorMovement = initialIds.filter((id) => id !== trace.targetId && finalIds.includes(id)).map((id) => {
    const start = baseline.items.find((item) => item.id === id)?.globalBoundsPhysical;
    const end = final.items.find((item) => item.id === id)?.globalBoundsPhysical;
    let previous = null, largestStep = null;
    for (const frame of trace.frames.filter((entry) => entry.atMs >= baseline.atMs && ["raf", "before-click", "final"].includes(entry.reason))) {
      // Preserve raw samples but never turn a mixed-time origin into a measured jump,
      // nor bridge over an uncertain interval as if both endpoints were adjacent.
      if (frame.geometryConsistency?.status !== "compatible") { previous = null; continue; }
      const bounds = frame.items.find((item) => item.id === id)?.globalBoundsPhysical;
      if (!bounds) continue;
      if (previous) {
        const dx = bounds.x - previous.bounds.x, dy = bounds.y - previous.bounds.y;
        const distance = Math.hypot(dx, dy);
        if (!largestStep || distance > largestStep.distancePhysical) largestStep = {
          fromMs: previous.atMs, toMs: frame.atMs, dx, dy, distancePhysical: distance,
          originSource: frame.nativeWindow?.source,
        };
      }
      previous = { bounds, atMs: frame.atMs };
    }
    const rawFinalDeltaPhysical = start && end ? { x: end.x - start.x, y: end.y - start.y } : null;
    return { id, finalDeltaPhysical: baseline.geometryConsistency?.status === "compatible"
      && final.geometryConsistency?.status === "compatible" ? rawFinalDeltaPhysical : null, rawFinalDeltaPhysical, largestStep };
  });
  const newIds = [...new Set(trace.frames.filter((frame) => frame.atMs >= trace.clickAtMs)
    .flatMap((frame) => frame.items.filter((item) => item.painted).map((item) => item.id))
    .filter((id) => id && !initialPaintedIds.includes(id)))];
  return { targetId: trace.targetId, clickAtMs: trace.clickAtMs, durationMs: trace.durationMs,
    frameCount: trace.frames.filter((frame) => frame.reason === "raf").length,
    uncertainFrameCount: trace.frames.filter((frame) => frame.geometryConsistency?.status !== "compatible").length,
    initialIds, initialPaintedIds, finalIds,
    targetExitingAtMs: trace.frames.find((frame) => frame.atMs >= trace.clickAtMs && frame.items.some((item) => item.id === trace.targetId
      && (item.state.removed === "true" || item.state.exiting === "true" || ["exiting", "exit", "removed"].includes(item.state.state))))?.atMs ?? null,
    targetAbsentAtMs: trace.frames.find((frame) => frame.atMs >= trace.clickAtMs && !frame.items.some((item) => item.id === trace.targetId))?.atMs ?? null,
    replenishment: newIds.map((id) => ({ id, initiallyInDom: initialIds.includes(id), firstDomAtMs: trace.frames.find((frame) => frame.items.some((item) => item.id === id))?.atMs,
      firstPaintedAtMs: trace.frames.find((frame) => frame.atMs >= trace.clickAtMs && frame.items.some((item) => item.id === id && item.painted))?.atMs ?? null })),
    survivorMovement, orderAndExitChanges: changes,
    nativeEvents: trace.events.reduce((counts, event) => ({ ...counts, [event.kind]: (counts[event.kind] ?? 0) + 1 }), {}),
    errors: trace.errors, droppedFrames: trace.droppedFrames, droppedEvents: trace.droppedEvents };
}

let closeTrace = null;
const network = [];
const networkRequests = new WeakMap();
const notificationRequest = (request) => new URL(request.url()).pathname.startsWith("/v1/notifications");
const onRequest = (request) => {
  if (!notificationRequest(request)) return;
  const entry = { path: new URL(request.url()).pathname, method: request.method(), requestedAt: Date.now() };
  network.push(entry); networkRequests.set(request, entry);
};
const onResponse = (response) => {
  const entry = networkRequests.get(response.request());
  if (entry) Object.assign(entry, { status: response.status(), responseAt: Date.now() });
};
const onFinished = (request) => {
  const entry = networkRequests.get(request);
  if (entry) entry.finishedAt = Date.now();
};
if (command === "trace-close") {
  bubble.on("request", onRequest); bubble.on("response", onResponse); bubble.on("requestfinished", onFinished);
}
try {
  if (command === "reload-bubble") {
    await bubble.reload({ waitUntil: "domcontentloaded" });
    await bubble.waitForTimeout(1200);
  }
  if (command === "reload-chat") {
    if (!chat) throw new Error("Expected chat WebView");
    await chat.reload({ waitUntil: "domcontentloaded" });
    await chat.waitForTimeout(600);
  }
  if (command === "reload-pet") {
    await pet.reload({ waitUntil: "domcontentloaded" });
    await pet.waitForFunction(() => Boolean(window.__AGENT_FRIEND_ANCHOR_DIAGNOSTICS__?.spriteScreen), { timeout: 10000 });
    await pet.waitForTimeout(600);
  }
  if (command === "hover") await hoverPet();
  if (command === "away") { await move(pet, 100, 100); await pet.waitForTimeout(700); }
  if (command === "wheel") {
    const dy = Number(args[0] ?? 120), dx = Number(args[1] ?? 0);
    const count = Number(args.find((arg) => arg.startsWith("--count="))?.slice(8) ?? 1);
    if (![dy, dx].every(Number.isFinite) || !Number.isInteger(count) || count < 1 || count > 10) throw new Error("wheel requires finite deltas and count 1..10");
    await hoverPet();
    const bar = await pet.locator('[data-anchor-diagnostics="actionbar"]').boundingBox();
    if (!bar) throw new Error("ActionBar is not visible");
    await move(pet, bar.x + bar.width / 2, bar.y + bar.height / 2);
    for (let index = 0; index < count; index++) { await pet.mouse.wheel(dx, dy); await pet.waitForTimeout(10); }
    await pet.waitForTimeout(700);
  }
  if (command === "action" || command === "button" || command === "tooltip") {
    if (command === "action" && !["short", "long", "stack", "clear"].includes(args[0])) throw new Error("Unknown debug action");
    if (command === "button" && !["打开设置", "缩小桌宠并移动到边上", "恢复桌宠大小"].includes(args[0])) throw new Error("Unknown pet control");
    await hoverPet();
    const target = command === "action" ? pet.locator(`[data-notification-debug="${args[0]}"]`) : pet.getByRole("button", { name: args[0], exact: true });
    for (let attempt = 0; attempt < 4; attempt++) {
      const inFrame = await target.evaluate((element) => {
        const r = element.getBoundingClientRect();
        const bar = document.querySelector('[data-anchor-diagnostics="actionbar"]').getBoundingClientRect();
        return r.x >= bar.x && r.y >= bar.y && r.right <= bar.right && r.bottom <= bar.bottom;
      });
      if (inFrame) break;
      const next = pet.getByRole("button", { name: "下一页", exact: true });
      if (await next.isEnabled()) await click(pet, next);
      else await click(pet, pet.getByRole("button", { name: "上一页", exact: true }));
      await pet.waitForTimeout(400);
    }
    if (command === "tooltip") {
      const rect = await target.boundingBox();
      if (!rect) throw new Error("Tooltip trigger is not rendered");
      await move(pet, rect.x + rect.width / 2, rect.y + rect.height / 2);
      await pet.locator('[data-slot="tooltip-content"][data-hit="actionbar-tooltip"]').waitFor({ state: "visible", timeout: 3000 });
    } else await click(pet, target);
    await pet.waitForTimeout(1200);
  }
  if (command === "drag") {
    await hoverPet();
    const [x, y] = args.map(Number);
    if (![x, y].every(Number.isFinite)) throw new Error("drag requires pet CSS x y");
    await pet.mouse.down();
    await pet.mouse.move(x, y, { steps: 30 });
    await move(pet, x, y);
    await pet.mouse.up();
    await pet.waitForTimeout(600);
  }
  if (["close", "trace-close", "expand"].includes(command)) {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,255}$/.test(args[0] ?? "")) throw new Error(`${command} requires an explicit notification ID`);
    const target = bubble.locator(`[data-notification-id="${args[0]}"]`).first()
      .locator('xpath=ancestor-or-self::*[@data-sonner-toast or @data-bubble-item][1]');
    if (command === "trace-close") await beginCloseTrace(args[0]);
    await click(bubble, command === "expand" ? target.getByRole("button", { name: "展开" }) : target.locator("[data-close-button]"),
      command === "trace-close" ? () => bubble.evaluate(() => window.__AGENT_FRIEND_NOTIFICATION_CLOSE_TRACE__.markClick()) : undefined);
    if (command === "trace-close") {
      // The close handler moves focus to the next live close button. Exercise
      // rapid keyboard closes without synthetic DOM events or hidden store calls.
      for (let index = 1; index < traceRepeat; index++) {
        await bubble.waitForTimeout(35);
        const focusedClose = await bubble.evaluate(() => document.activeElement?.matches('[data-close-button]:not(:disabled)'));
        if (!focusedClose) throw new Error("Rapid close lost focus on a live close control");
        await bubble.keyboard.press("Enter");
      }
    }
    await bubble.waitForTimeout(command === "trace-close" ? traceDuration : 700);
    if (command === "trace-close") {
      closeTrace = await bubble.evaluate(() => window.__AGENT_FRIEND_NOTIFICATION_CLOSE_TRACE__.finish());
      closeTrace.requestedPostClickMs = traceDuration;
      closeTrace.closeCount = traceRepeat;
      closeTrace.network = network.map((entry) => ({ ...entry,
        requestedAtMs: entry.requestedAt - Date.parse(closeTrace.startedAt),
        responseAtMs: entry.responseAt ? entry.responseAt - Date.parse(closeTrace.startedAt) : null,
      }));
      closeTrace.summary = summarizeCloseTrace(closeTrace);
    }
  }
  let settings = null;
  if (command === "settings") {
    if (!chat || !["inspect", "off", "on"].includes(args[0])) throw new Error("settings requires inspect/off/on and chat WebView");
    if (!(await chat.getByRole("button", { name: "通知", exact: true }).isVisible())) {
      await click(chat, chat.getByRole("button", { name: "设置", exact: true }));
    }
    await click(chat, chat.getByRole("button", { name: "通知", exact: true }));
    const control = chat.getByRole("switch", { name: "气泡提示音", exact: true });
    await control.waitFor({ state: "visible", timeout: 10000 });
    const desired = args[0] === "on";
    if (args[0] !== "inspect" && (await control.getAttribute("aria-checked") === "true") !== desired) {
      await click(chat, control);
      await chat.waitForTimeout(600);
    }
    const current = await native(chat, "get_setting");
    settings = { soundEnabled: await control.getAttribute("aria-checked"), nativeSoundEnabled: current.notificationSoundEnabled,
      sourceDefaultVisible: await chat.getByText("默认", { exact: true }).isVisible() };
  }
  const report = {
    at: new Date().toISOString(), command, settings, closeTrace, pages: await Promise.all(pages.map(async (p) => ({ url: p.url(), title: await p.title() }))),
    anchor: await anchor(), context: await native(pet, "get_pet_accessory_context"),
    presentation: await native(bubble, "get_bubble_presentation_state"),
    savedActionBarPage: (await native(pet, "get_setting")).petActionBarPage,
    bubbleWindow: await bubble.evaluate(async () => {
      const { getCurrentWindow } = await import("/node_modules/@tauri-apps/api/window.js");
      const win = getCurrentWindow();
      return { position: await win.outerPosition(), size: await win.outerSize(), scaleFactor: await win.scaleFactor(), visible: await win.isVisible() };
    }),
    pet: await pet.evaluate(() => ({
      actionBarPage: document.querySelector("[data-actionbar-page]")?.getAttribute("data-actionbar-page") ?? null,
      dpr: devicePixelRatio, width: innerWidth, height: innerHeight, bodyPointerEvents: getComputedStyle(document.body).pointerEvents,
      buttons: [...document.querySelectorAll("button")].map((e) => ({ label: e.getAttribute("aria-label"), debug: e.getAttribute("data-notification-debug"), rect: e.getBoundingClientRect().toJSON() })),
      tooltips: [...document.querySelectorAll('[data-slot="tooltip-content"][data-hit="actionbar-tooltip"]')].map((e) => ({
        side: e.getAttribute("data-side"), text: e.textContent, rect: e.getBoundingClientRect().toJSON(),
        maxWidth: getComputedStyle(e).maxWidth, maxHeight: getComputedStyle(e).maxHeight,
      })),
    })),
    bubble: await bubble.evaluate((includeText) => ({
      dpr: devicePixelRatio, width: innerWidth, height: innerHeight,
      toasts: [...document.querySelectorAll("[data-sonner-toast],[data-bubble-item]")].map((e) => ({
        visible: e.dataset.visible, removed: e.dataset.removed, rect: e.getBoundingClientRect().toJSON(), ...(includeText ? { text: e.textContent } : {}),
        id: e.getAttribute("data-notification-id") ?? e.querySelector("[data-notification-id]")?.getAttribute("data-notification-id"),
        close: e.querySelector("[data-close-button]")?.getBoundingClientRect().toJSON(),
        closeStyle: e.querySelector("[data-close-button]") ? (() => { const s=getComputedStyle(e.querySelector("[data-close-button]")); return {left:s.left,top:s.top,transform:s.transform}; })() : null,
        toasterClass: e.closest("[data-sonner-toaster],[data-bubble-viewport],[data-bubble-stack]")?.className,
      })),
    }), command !== "trace-close"),
  };
  const directory = resolve(args.find((arg) => arg.startsWith("--output="))?.slice(9) ?? `${process.env.TEMP ?? "/tmp"}/agent-friend-notification-visual-117`);
  await mkdir(directory, { recursive: true });
  const key = `${Date.now()}-${command}`;
  await writeFile(resolve(directory, `${key}.json`), JSON.stringify(report, null, 2));
  await bubble.screenshot({ path: resolve(directory, `${key}-bubble.png`) });
  if (command === "tooltip") await pet.screenshot({ path: resolve(directory, `${key}-pet.png`) });
  console.log(JSON.stringify({ report: resolve(directory, `${key}.json`), command, settings,
    actionBarPage: report.pet.actionBarPage, savedActionBarPage: report.savedActionBarPage,
    bubbleWindow: report.bubbleWindow, trace: closeTrace?.summary,
    tooltips: report.pet.tooltips,
    notifications: report.bubble.toasts.map(({ id, visible, removed }) => ({ id, visible, removed })),
  }, null, 2));
} finally {
  bubble.off("request", onRequest); bubble.off("response", onResponse); bubble.off("requestfinished", onFinished);
  if (command === "trace-close") await bubble.evaluate(() => window.__AGENT_FRIEND_NOTIFICATION_CLOSE_TRACE__?.abort()).catch(() => undefined);
  await browser.close();
}
