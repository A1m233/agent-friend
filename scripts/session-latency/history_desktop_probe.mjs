// Attach-only acceptance probe. The caller owns runtime launch, freshness and shutdown.
// No sends, provider calls, tool execution, store mutations, or installed-app access.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, stat, realpath } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

function observedRequestKind(value) {
  let url;
  try { url = new URL(value); } catch { return null; }
  if (/^\/v1\/sessions\/[^/]+\/history(?:\/content)?$/.test(url.pathname)) {
    return url.pathname.endsWith("/content") ? "content" : "history";
  }
  return url.pathname === "/v1/sessions/search" ? "search" : null;
}

// Pure offline contract check: no fixture reads, runtime lease, Playwright or CDP.
if (process.argv.slice(2).join() === "--self-test-url-classification") {
  const cases = [
    ["/v1/sessions/example/history", "history"],
    ["/v1/sessions/example/history?before=cursor", "history"],
    ["/v1/sessions/example/history?around=uuid", "history"],
    ["/v1/sessions/example/history?after=cursor&message_id=id", "history"],
    ["/v1/sessions/example/history/content?ref=uuid&offset=0", "content"],
    ["/v1/sessions/search?q=history", "search"],
    ["/src/stores/historyProjection.ts", null],
    ["/src/stores/historyLifecycle.ts?t=1", null],
    ["/src/services/historyContent.ts", null],
    ["/src/services/historyObservation.ts", null],
    ["/src/stores/historyLiveEvents.ts", null],
    ["/v1/sessions/example/history/extra", null],
    ["/v1/sessions/example/history/content/extra", null],
    ["/prefix/v1/sessions/example/history", null],
    ["/v1/sessions/example", null],
    ["/v1/sessions/search/extra", null],
  ];
  for (const [path, expected] of cases) assert.equal(observedRequestKind(`http://127.0.0.1:1420${path}`), expected);
  assert.equal(observedRequestKind("not-a-url"), null);
  console.log(JSON.stringify({ status: "passed", url_classification_assertions: cases.length + 1 }));
  process.exit(0);
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "frontend/package.json"));
const { chromium } = require("playwright");
const hash = (value) => createHash("sha256").update(value).digest("hex");
const args = process.argv.slice(2);
const options = {};
for (let i = 0; i < args.length; i += 2) {
  if (!["--cdp", "--identity", "--output", "--focus"].includes(args[i]) || !args[i + 1]
    || options[args[i]]) throw new Error("invalid_probe_arguments");
  options[args[i]] = args[i + 1];
}
if (!["--cdp", "--identity", "--output"].every((key) => options[key])) throw new Error("missing_probe_arguments");
if (options["--focus"] && options["--focus"] !== "long-tools") throw new Error("invalid_probe_focus");
const cdp = new URL(options["--cdp"]);
if (cdp.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(cdp.hostname)) {
  throw new Error("probe_requires_local_cdp");
}
const identityBytes = await readFile(resolve(options["--identity"]));
const identity = JSON.parse(identityBytes.toString("utf8"));
const expectedKinds = ["fixed_gb", "long", "short"];
if (identity.schema_version !== 1 || identity.kind !== "isolated_desktop_history_fixture"
  || identity.original?.unchanged !== true || identity.sessions?.length !== 3
  || identity.sessions.map((s) => s.kind).sort().join() !== expectedKinds.join()) {
  throw new Error("invalid_fixture_identity");
}
const fixtures = Object.fromEntries(identity.sessions.map((s) => [s.kind, s]));
const expectedIds = identity.sessions.map((s) => s.session_id).sort();
if (new Set(expectedIds).size !== 3 || expectedIds.some((id) => !/^[a-f0-9-]{36}$/.test(id))) {
  throw new Error("invalid_fixture_session_ids");
}
// Use the canonical runtime guard to locate and validate the lease; do not invent paths.
const lease = JSON.parse(execFileSync("uv", ["run", "python", "-c", [
  "import json,sys",
  "sys.path.insert(0,sys.argv[1])",
  "from runtime_guard import runtime_state_dir,process_start_id",
  "v=json.loads((runtime_state_dir()/'lease.json').read_text(encoding='utf-8'))",
  "assert process_start_id(int(v['ownerPid'])) == v['ownerStartId'], 'stale_runtime_lease'",
  "print(json.dumps(v))",
].join(";"), resolve(root, "scripts/dev")], { cwd: root, encoding: "utf8", windowsHide: true }));
const normalizedPath = (value) => process.platform === "win32" ? value.toLowerCase() : value;
const branch = execFileSync("git", ["branch", "--show-current"], { cwd: root, encoding: "utf8" }).trim();
if (normalizedPath(await realpath(lease.worktree)) !== normalizedPath(await realpath(root))
  || lease.branch !== branch || lease.mode !== "tauri" || !lease.ports.includes(Number(cdp.port))) {
  throw new Error("runtime_identity_mismatch");
}
const sourcePaths = execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
  "frontend/src", "frontend/src-tauri/src", "agent/src", "agent_bridge/src", "runtime_config/src"],
{ cwd: root, encoding: "utf8" }).split("\0").filter(Boolean);
let sourceNewest = 0;
for (const path of sourcePaths) {
  try { sourceNewest = Math.max(sourceNewest, (await stat(resolve(root, path))).mtimeMs); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
}
if (!Number.isFinite(Date.parse(lease.startedAt)) || sourceNewest > Date.parse(lease.startedAt)) {
  throw new Error("runtime_lease_predates_source_changes");
}
const output = resolve(options["--output"]);
await mkdir(output, { recursive: false });
const report = {
  schema_version: 1, kind: "desktop_history_ui_probe", status: "running", started_at: new Date().toISOString(),
  identity_sha256: hash(identityBytes), script_sha256: hash(await readFile(fileURLToPath(import.meta.url))),
  runtime: { worktree_sha256: hash(normalizedPath(root)), branch_sha256: hash(branch), owner_pid: lease.ownerPid,
    started_at: lease.startedAt, source_newest_at: new Date(sourceNewest).toISOString(), cdp_port: Number(cdp.port) },
  evidence: "real_tauri_webview_dom_and_interaction", screenshot_kind: "complete_webview_viewport_without_native_titlebar",
  limits: ["No send, tool execution, provider invocation or product-state injection.",
    "Lease freshness is a guard; caller must also verify resident-process source freshness.",
    "DOM marker geometry proves viewport visibility, not native-window/GPU rendering.",
    "JSON excludes message text, titles, request URLs and clipboard plaintext."],
  steps: [], requests: [], screenshots: [], failures: [], prohibited_request_count: 0,
  focus: options["--focus"] ?? "complete", scroll_probes: [],
  voice_prewarm_network_blocked: { enabled: true, blocked_request_count: 0,
    scope: "only_local_voice_transcriptions_prewarm_after_cdp_attachment", response_status: "disabled" },
};
const save = () => writeFile(resolve(output, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
await save();
let browser;
let page;
let identityConfirmed = false;
let clipboardBackupKey = null;
const pendingNetwork = new Set();
const starts = new WeakMap();
function summarizeHistoryResponseBudget() {
  const pages = report.requests.filter((request) => request.kind === "history"
    && request.status >= 200 && request.status < 300);
  report.history_response_budget = { limit_bytes: 262144, checked_count: pages.length,
    largest_response_bytes: Math.max(0, ...pages.map((request) => request.bytes)),
    exceeded_count: pages.filter((request) => request.bytes > 262144).length,
    measurement: "complete_decoded_response_body_bytes" };
}
const fail = (code) => { const error = new Error(code); error.probeCode = code; throw error; };
const requireCondition = (condition, code) => { if (!condition) fail(code); };
const row = (id) => page.locator(`[data-message-id="${id}"]`);
const viewport = () => page.locator(".chat-message-viewport");
async function snapshot() {
  return page.evaluate(async () => {
    const state = (await import("/src/stores/conversation.ts")).useConversationStore.getState();
    const view = document.querySelector(".chat-message-viewport");
    const bounds = view?.getBoundingClientRect();
    const visibleIds = [...document.querySelectorAll("[data-message-id]")].filter((element) => {
      const rect = element.getBoundingClientRect();
      return bounds && rect.height > 0 && rect.bottom > bounds.top && rect.top < bounds.bottom;
    }).map((element) => element.getAttribute("data-message-id"));
    return { session_id: state.currentSessionId, loading: state.historyLoading,
      message_count: state.messages.length, message_ids: state.messages.map((m) => m.id), visible_ids: visibleIds,
      deferred_count: state.messages.filter((m) => m.history && (m.history.event.body.state === "deferred"
        || m.history.event.result_event?.body.state === "deferred")).length,
      history_first_ids: state.messages.filter((m) => m.history?.first).map((m) => m.id),
      has_before: state.hasOlderHistory(), has_after: state.hasNewerHistory(),
      active_run_count: Object.values(state.runSnapshots).filter((r) => r.status !== "terminal").length,
      scroll_top: view?.scrollTop ?? null, scroll_height: view?.scrollHeight ?? null,
      viewport_height: view?.clientHeight ?? null };
  });
}
async function waitReady(id, timeout = 45000) {
  await page.waitForFunction(async (sessionId) => {
    const state = (await import("/src/stores/conversation.ts")).useConversationStore.getState();
    return state.currentSessionId === sessionId && !state.historyLoading && state.messages.length > 0;
  }, id, { timeout });
  await viewport().waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const view = document.querySelector(".chat-message-viewport")?.getBoundingClientRect();
    return view && [...document.querySelectorAll("[data-message-id]")].some((node) => {
      const rect = node.getBoundingClientRect();
      return rect.height > 0 && rect.top < view.bottom && rect.bottom > view.top;
    });
  });
  return snapshot();
}
async function screenshot(name) {
  const filename = `${name}.png`;
  await page.screenshot({ path: resolve(output, filename), fullPage: false });
  report.screenshots.push({ name: filename, sha256: hash(await readFile(resolve(output, filename))) });
}
async function step(name, callback, optional = false) {
  if (options["--focus"] && !["long_latest_bounded", "long_load_older_by_scroll", "long_unified_disclosure",
    "long_first_tool_full_body", "long_last_tool_full_body"].includes(name)) return;
  const began = Date.now();
  const entry = { name, status: "running" };
  report.steps.push(entry);
  try { entry.result = await callback(); entry.status = "passed"; }
  catch (error) {
    const fatal = error.probeCode === "clipboard_restore_failed";
    entry.status = optional && !fatal ? "not_verified" : "failed";
    entry.error_code = error.probeCode ?? "ui_operation_failed";
    entry.error_type = error.name;
    if (error.geometry) entry.geometry = error.geometry;
    if (identityConfirmed) { try { await screenshot(`failure-${name}`); } catch { /* Retain scoped diagnostics. */ } }
    if (!optional || fatal) report.failures.push(name);
    if (fatal) throw error; // Do not reload the page and discard its only clipboard backup.
  } finally { entry.duration_ms = Date.now() - began; await save(); }
}
async function openFixture(fixture) {
  const sidebar = page.locator('[data-sidebar="menu-button"]').filter({ hasText: fixture.title });
  await sidebar.first().waitFor({ state: "visible" });
  requireCondition(await sidebar.count() === 1, "fixture_sidebar_not_unique");
  await sidebar.click();
  return waitReady(fixture.session_id);
}
async function wheel(delta, box) {
  const original = box ?? await viewport().boundingBox();
  const screen = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  const bounds = original && { x: Math.max(0, original.x), y: Math.max(0, original.y),
    width: Math.min(screen.width, original.x + original.width) - Math.max(0, original.x),
    height: Math.min(screen.height, original.y + original.height) - Math.max(0, original.y) };
  requireCondition(bounds?.width > 0 && bounds?.height > 0, "scroll_viewport_unavailable");
  // Main viewport: use its empty left margin, outside nested tool scrollers.
  // Nested output: avoid the page's centered floating jump-to-bottom button.
  const point = { x: bounds.x + Math.min(box ? 12 : 4, bounds.width / 4),
    y: bounds.y + (box ? Math.min(24, bounds.height / 4) : bounds.height / 2) };
  const readHit = () => page.evaluate((point) => {
    const hit = document.elementFromPoint(point.x, point.y);
    const containers = [];
    for (let element = hit; element; element = element.parentElement) {
      if (element.scrollHeight > element.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(element).overflowY)) {
        containers.push({ tag: element.tagName, slot: element.getAttribute("data-slot"),
          main: element.classList.contains("chat-message-viewport"), scroll_top: element.scrollTop,
          scroll_height: element.scrollHeight, client_height: element.clientHeight });
      }
    }
    return { hit_tag: hit?.tagName, hit_scroll_bottom_button: !!hit?.closest('button[aria-label="滚到底部"]'), containers };
  }, point);
  const before = options["--focus"] ? await readHit() : undefined;
  await page.mouse.move(point.x, point.y);
  await page.mouse.wheel(0, delta);
  await page.waitForTimeout(180);
  if (before && report.scroll_probes.length < 160) report.scroll_probes.push({ inner: !!box, delta, point, before, after: await readHit() });
}
async function findRow(id, direction = 1) {
  for (let attempt = 0; attempt < 35; attempt++) {
    if (await row(id).count()) { await row(id).scrollIntoViewIfNeeded(); return; }
    await wheel(direction * 1600);
  }
  fail("message_row_not_reached");
}
// Read scoped text-node ranges (including open WebComponent shadows), never body.innerText.
async function markerGeometry(id, marker, select = false) {
  return row(id).evaluate((element, args) => {
    function find(root) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.nodeType === Node.TEXT_NODE) {
          const at = node.textContent.indexOf(args.marker);
          if (at >= 0) return { node, at };
        } else if (node.shadowRoot) { const hit = find(node.shadowRoot); if (hit) return hit; }
      }
      return null;
    }
    const hit = find(element);
    if (!hit) return { found: false };
    const range = document.createRange();
    range.setStart(hit.node, hit.at); range.setEnd(hit.node, hit.at + args.marker.length);
    const rect = range.getBoundingClientRect();
    if (args.select) { const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      document.dispatchEvent(new Event("selectionchange")); }
    let parent = hit.node.parentElement;
    let clip = { top: 0, left: 0, right: innerWidth, bottom: innerHeight };
    const scrollables = [];
    while (parent) {
      const style = getComputedStyle(parent);
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
        const box = parent.getBoundingClientRect();
        clip = { top: Math.max(clip.top, box.top), bottom: Math.min(clip.bottom, box.bottom),
          left: Math.max(clip.left, box.left), right: Math.min(clip.right, box.right) };
        if (parent.scrollHeight > parent.clientHeight + 1 && /(auto|scroll)/.test(style.overflowY)) {
          scrollables.push({ x: box.x, y: box.y, width: box.width, height: box.height,
            scroll_top: parent.scrollTop, max_scroll_top: parent.scrollHeight - parent.clientHeight,
            element: parent });
        }
      }
      parent = parent.parentElement ?? parent.getRootNode()?.host;
    }
    const towardStart = rect.top < clip.top;
    // Once the output reaches its edge, scroll its clipping parent rather than
    // repeatedly sending wheel events into an already exhausted inner scroller.
    const target = scrollables.find((candidate) => towardStart ? candidate.scroll_top > 0.5
      : candidate.scroll_top < candidate.max_scroll_top - 0.5);
    const centerHit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    const unobscured = !!centerHit && element.contains(centerHit);
    const targetHit = target && document.elementFromPoint(target.x + Math.min(12, target.width / 4),
      Math.max(0, target.y) + Math.min(24, Math.min(innerHeight - Math.max(0, target.y), target.height) / 4));
    const targetReachable = !!targetHit && target.element.contains(targetHit);
    const targetBox = target && { x: target.x, y: target.y, width: target.width, height: target.height,
      scroll_top: target.scroll_top, max_scroll_top: target.max_scroll_top };
    return { found: true, visible: rect.height > 0 && rect.top >= clip.top && rect.bottom <= clip.bottom
      && rect.left >= clip.left && rect.right <= clip.right && unobscured,
      unobscured, y: rect.y, height: rect.height, clip, scroll_target: targetBox,
      scroll_target_reachable: targetReachable };
  }, { marker, select });
}
async function showMarker(id, marker, shot) {
  await row(id).waitFor({ state: "attached" });
  let lastGeometry;
  for (let attempt = 0; attempt < 24; attempt++) {
    const result = await markerGeometry(id, marker);
    lastGeometry = result;
    if (result.found && result.visible) { if (shot) await screenshot(shot); return { marker_sha256: hash(marker), ...result }; }
    if (!result.found) { await page.waitForTimeout(200); continue; }
    const delta = result.y + result.height / 2 - (result.clip.top + result.clip.bottom) / 2;
    const target = result.scroll_target;
    // The composer can cover content despite its DOM rectangle being in the viewport.
    // First bring the actual wheel target above that overlay using the main margin.
    if (target && !result.scroll_target_reachable) {
      const view = await viewport().boundingBox();
      await wheel(target.y - (view.y + view.height / 3));
    } else if (!result.unobscured && result.y >= result.clip.top && result.y + result.height <= result.clip.bottom) {
      const view = await viewport().boundingBox();
      await wheel(result.y - (view.y + view.height / 3));
    } else await wheel(Math.max(-20000, Math.min(20000, delta)), target);
  }
  const error = new Error("marker_not_visible_in_viewport");
  error.probeCode = "marker_not_visible_in_viewport";
  error.geometry = lastGeometry;
  throw error;
}
async function expandRun(fixture) {
  let control = page.getByRole("button", { name: "展开执行过程", exact: true });
  for (let attempt = 0; attempt < 30 && await control.count() === 0; attempt++) await wheel(-2000);
  if (await control.count()) {
    requireCondition(await control.count() === 1, "multiple_run_disclosures");
    await control.click();
  }
  requireCondition(await page.getByRole("button", { name: "收起执行过程", exact: true }).count() === 1,
    "run_disclosure_did_not_expand");
  const state = await snapshot();
  requireCondition(state.session_id === fixture.session_id && state.history_first_ids.length === 2,
    "logical_run_header_not_unique");
  return state;
}
async function loadDetails(id) {
  await findRow(id);
  const button = row(id).getByRole("button", { name: "加载完整内容", exact: true });
  if (await button.count()) {
    await button.click();
    await button.waitFor({ state: "hidden", timeout: 45000 });
  }
  requireCondition(await row(id).getByRole("button", { name: "重新加载内容", exact: true }).count() === 0,
    "deferred_detail_failed");
  // Hydration returns the ordinary tool activity row, whose own details start collapsed.
  if (fixtures.long.expected.tools.some((tool) => tool.request_uuid === id)) {
    const toolDetails = row(id).locator('[data-slot="collapsible-trigger"]');
    await toolDetails.waitFor({ state: "visible" });
    if (await toolDetails.getAttribute("aria-expanded") !== "true") await toolDetails.click();
    await row(id).locator('[data-slot="collapsible-trigger"][aria-expanded="true"]').waitFor();
  }
}
async function copyAnswer(fixture) {
  const id = fixture.expected.answer_uuid;
  await findRow(id);
  const button = row(id).getByRole("button", { name: "复制消息", exact: true });
  const key = `__historyProbeClipboard_${Date.now()}`;
  const preserved = await page.evaluate(async (backupKey) => {
    if (typeof navigator.clipboard?.read !== "function" || typeof navigator.clipboard?.write !== "function"
      || typeof ClipboardItem !== "function") return false;
    try {
      const boundedRead = (promise) => Promise.race([promise,
        new Promise((_, reject) => setTimeout(() => reject(new Error("clipboard_read_timeout")), 2000))]);
      // A WebView may leave a native permission prompt pending indefinitely.
      // Do not prompt or grant permissions merely for an optional copy assertion.
      const readPermission = await boundedRead(navigator.permissions.query({ name: "clipboard-read" }));
      const writePermission = await boundedRead(navigator.permissions.query({ name: "clipboard-write" }));
      if (readPermission.state !== "granted" || writePermission.state !== "granted") return false;
      const originals = await boundedRead(navigator.clipboard.read());
      // Eagerly own every available Blob before the product changes the global clipboard.
      // Keep all formats/items in this WebView's memory; never serialize this backup.
      const items = await boundedRead(Promise.all(originals.map(async (item) => new ClipboardItem(Object.fromEntries(
        await Promise.all(item.types.map(async (type) => [type, await item.getType(type)])),
      ), { presentationStyle: item.presentationStyle }))));
      const restorable = items.length ? items : [new ClipboardItem({
        "text/plain": new Blob([""], { type: "text/plain" }),
      })];
      // Prove write/restore support with the original data before clicking Copy.
      await navigator.clipboard.write(restorable);
      window[backupKey] = restorable;
      return true;
    } catch { return false; }
  }, key);
  requireCondition(preserved, "clipboard_preservation_unavailable");
  clipboardBackupKey = key;
  try {
    await button.click();
    await row(id).getByRole("button", { name: "已复制", exact: true }).waitFor({ timeout: 45000 });
    const clipboard = await page.evaluate(async () => {
      try {
        const bytes = new TextEncoder().encode(await navigator.clipboard.readText());
        const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
        return { bytes: bytes.length, sha256: [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("") };
      } catch { return null; }
    });
    requireCondition(clipboard !== null, "clipboard_read_permission_unavailable");
    requireCondition(clipboard.sha256 === fixture.expected.answer_sha256, "complete_message_copy_hash_mismatch");
    return { ...clipboard, clipboard_restored: true };
  } finally {
    requireCondition(await restoreClipboard(), "clipboard_restore_failed");
  }
}
async function restoreClipboard() {
  if (!clipboardBackupKey) return true;
  try {
    const restored = await page.evaluate(async (key) => {
      const items = window[key];
      if (!items) return false;
      try {
        await navigator.clipboard.write(items);
        delete window[key];
        return true;
      } catch { return false; }
    }, clipboardBackupKey);
    if (restored) clipboardBackupKey = null;
    return restored;
  } catch { return false; }
}
try {
  browser = await chromium.connectOverCDP(cdp.href, { noDefaults: true });
  // Composer activation can lazily start voice even in a --no-voice runtime.
  // Only this unrelated prewarm endpoint is stubbed; history and other APIs stay real.
  for (const context of browser.contexts()) {
    await context.route((url) => ["localhost", "127.0.0.1"].includes(url.hostname)
      && url.pathname === "/voice/transcriptions/prewarm", async (route) => {
      report.voice_prewarm_network_blocked.blocked_request_count++;
      let traceId = "history-desktop-probe";
      try { traceId = route.request().postDataJSON()?.traceId ?? traceId; } catch { /* Preflight has no body. */ }
      await route.fulfill({ status: 200, contentType: "application/json",
        headers: { "access-control-allow-origin": "*", "access-control-allow-methods": "POST, OPTIONS",
          "access-control-allow-headers": "content-type" },
        body: JSON.stringify({ status: "disabled", traceId, ttlMs: 0 }) });
    });
  }
  const candidates = browser.contexts().flatMap((context) => context.pages()).filter((candidate) => {
    try { const url = new URL(candidate.url()); return url.pathname === "/chat.html"
      && ["localhost", "127.0.0.1"].includes(url.hostname) && lease.ports.includes(Number(url.port)); }
    catch { return false; }
  });
  requireCondition(candidates.length === 1, "real_chat_webview_not_unique");
  page = candidates[0]; page.setDefaultTimeout(12000);
  requireCondition(await page.evaluate(() => !!window.__TAURI_INTERNALS__), "not_a_tauri_webview");
  const remoteIds = await page.evaluate(async () => (await (await import("/src/services/api/sessions.ts"))
    .sessionsApi.list()).map((s) => s.session_id).sort());
  requireCondition(remoteIds.join() === expectedIds.join(), "runtime_fixture_sessions_mismatch");
  report.runtime.page_url_sha256 = hash(page.url());
  report.runtime.session_ids = remoteIds;
  requireCondition((await snapshot()).active_run_count === 0, "active_run_present");
  identityConfirmed = true;
  await page.bringToFront();
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() !== "GET" && /^\/v1\/(runs|sessions|tools|command-runtime)(\/|$)/.test(url.pathname)) {
      report.prohibited_request_count++;
    }
    if (observedRequestKind(request.url())) starts.set(request, Date.now());
  });
  page.on("requestfinished", (request) => {
    if (!starts.has(request) || report.requests.length >= 3000) return;
    const promise = (async () => {
      const response = await request.response();
      const url = new URL(request.url());
      const headers = response?.headers() ?? {};
      const bytes = response ? (await response.body()).length : 0;
      report.requests.push({ kind: observedRequestKind(request.url()),
      direction: url.searchParams.has("around") ? "around" : url.searchParams.has("before") ? "before"
        : url.searchParams.has("after") ? "after" : "latest",
      message_filtered: url.searchParams.has("message_id"), status: response?.status(), bytes,
      duration_ms: Date.now() - starts.get(request), request_id: headers["x-agent-friend-request-id"] ?? null,
      navigation_id: request.headers()["x-agent-friend-navigation-id"] ?? null });
    })().catch(() => { report.network_observation_errors = (report.network_observation_errors ?? 0) + 1; });
    pendingNetwork.add(promise); void promise.finally(() => pendingNetwork.delete(promise));
  });
  page.on("requestfailed", (request) => {
    if (starts.has(request)) report.requests.push({ kind: "failed_read", duration_ms: Date.now() - starts.get(request) });
  });
  if (options["--focus"]) {
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(async () => {
      const state = (await import("/src/stores/conversation.ts")).useConversationStore.getState();
      return state.currentSessionId && !state.historyLoading && state.messages.length > 0;
    });
  }

  await step("short_latest_visible", async () => {
    const state = await openFixture(fixtures.short);
    const begin = await showMarker(fixtures.short.expected.answer_uuid, `${fixtures.short.expected.answer_marker}_BEGIN`);
    const end = await showMarker(fixtures.short.expected.answer_uuid, `${fixtures.short.expected.answer_marker}_END`, "01-short-complete");
    return { state, begin, end };
  });
  await step("short_complete_copy", () => copyAnswer(fixtures.short), true);
  await step("restore_last_session_after_webview_reload", async () => {
    await page.reload({ waitUntil: "domcontentloaded" });
    const state = await waitReady(fixtures.short.session_id);
    const end = await showMarker(fixtures.short.expected.answer_uuid, `${fixtures.short.expected.answer_marker}_END`,
      "01b-restored-short");
    return { state, end, scope: "frontend_reload_restore_not_process_restart" };
  });
  await step("long_latest_bounded", async () => {
    const state = await openFixture(fixtures.long);
    requireCondition(state.message_count <= 80 && state.deferred_count > 0 && state.has_before,
      "long_latest_not_bounded_or_deferred");
    await screenshot("02-long-latest"); return state;
  });
  await step("long_load_older_by_scroll", async () => {
    const before = await snapshot();
    for (let attempt = 0; attempt < 35 && (await snapshot()).has_before; attempt++) await wheel(-3000);
    const after = await snapshot();
    requireCondition(!after.has_before && after.message_count === fixtures.long.expected.display_items,
      "long_older_page_not_complete");
    requireCondition(new Set(after.message_ids).size === after.message_count, "duplicate_message_rows");
    requireCondition(after.message_ids.includes(fixtures.long.expected.user_uuid)
      && fixtures.long.expected.tools.every((tool) => after.message_ids.includes(tool.request_uuid)),
    "long_tool_or_user_anchor_missing");
    await screenshot("03-long-earliest"); return { before, after };
  });
  await step("long_unified_disclosure", async () => {
    const expanded = await expandRun(fixtures.long);
    await screenshot("04-long-expanded");
    await page.getByRole("button", { name: "收起执行过程", exact: true }).click();
    await page.getByRole("button", { name: "展开执行过程", exact: true }).waitFor();
    const collapsed = await snapshot();
    await page.getByRole("button", { name: "展开执行过程", exact: true }).click();
    return { expanded, collapsed };
  });
  await step("long_first_tool_full_body", async () => {
    const tool = fixtures.long.expected.tools[0];
    await loadDetails(tool.request_uuid);
    const begin = await showMarker(tool.request_uuid, `${tool.marker}_BEGIN`, "05-tool-first-begin");
    const end = await showMarker(tool.request_uuid, `${tool.marker}_END`, "06-tool-first-end");
    return { request_uuid: tool.request_uuid, result_uuid: tool.result_uuid, begin, end };
  });
  await step("long_last_tool_full_body", async () => {
    const tool = fixtures.long.expected.tools.at(-1);
    await loadDetails(tool.request_uuid);
    const begin = await showMarker(tool.request_uuid, `${tool.marker}_BEGIN`, "07-tool-last-begin");
    const end = await showMarker(tool.request_uuid, `${tool.marker}_END`, "08-tool-last-end");
    return { request_uuid: tool.request_uuid, result_uuid: tool.result_uuid, begin, end };
  });
  await step("long_answer_full_body", async () => {
    await findRow(fixtures.long.expected.answer_uuid);
    await loadDetails(fixtures.long.expected.answer_uuid);
    const begin = await showMarker(fixtures.long.expected.answer_uuid, `${fixtures.long.expected.answer_marker}_BEGIN`, "09-long-answer-begin");
    const end = await showMarker(fixtures.long.expected.answer_uuid, `${fixtures.long.expected.answer_marker}_END`, "10-long-answer-end");
    return { begin, end, state: await snapshot() };
  });
  await step("long_complete_copy", () => copyAnswer(fixtures.long), true);
  await step("fixed_gb_latest_visible", async () => {
    const state = await openFixture(fixtures.fixed_gb);
    requireCondition(state.message_count <= 80, "gb_latest_item_budget_exceeded");
    await screenshot("11-fixed-gb-latest"); return state;
  });
  await step("fixed_gb_older_page_by_scroll", async () => {
    const before = await snapshot();
    requireCondition(before.session_id === fixtures.fixed_gb.session_id && before.has_before,
      "gb_older_page_unavailable");
    let after = before;
    for (let attempt = 0; attempt < 35 && after.message_ids[0] === before.message_ids[0]; attempt++) {
      await wheel(-6000); after = await snapshot();
    }
    requireCondition(after.message_ids[0] !== before.message_ids[0] && after.message_count <= 512,
      "gb_older_page_not_reached_or_unbounded");
    await screenshot("12-fixed-gb-older"); return { before, after };
  });
  await step("switch_back_to_short", async () => {
    const state = await openFixture(fixtures.short);
    const marker = await showMarker(fixtures.short.expected.answer_uuid, `${fixtures.short.expected.answer_marker}_END`, "13-switch-short");
    return { state, marker };
  });
  await step("search_around_long_answer", async () => {
    await page.keyboard.press(process.platform === "darwin" ? "Meta+Shift+f" : "Control+Shift+f");
    const dialog = page.getByRole("dialog", { name: "搜索全部对话", exact: true });
    await dialog.waitFor();
    await dialog.getByLabel("搜索全部对话", { exact: true }).fill(`${fixtures.long.expected.answer_marker}_END`);
    const option = dialog.getByRole("option").filter({ hasText: `${fixtures.long.expected.answer_marker}_END` });
    await option.first().waitFor({ timeout: 45000 });
    requireCondition(await option.count() === 1, "search_fixture_result_not_unique");
    await option.click(); await dialog.waitFor({ state: "hidden" });
    await waitReady(fixtures.long.session_id);
    const end = await showMarker(fixtures.long.expected.answer_uuid, `${fixtures.long.expected.answer_marker}_END`, "14-search-around");
    return { state: await snapshot(), end };
  }, true);
  await step("quoted_selection_and_source_navigation", async () => {
    await page.keyboard.press("Escape");
    await openFixture(fixtures.short);
    const id = fixtures.short.expected.answer_uuid;
    const marker = `${fixtures.short.expected.answer_marker}_BEGIN`;
    await showMarker(id, marker);
    await markerGeometry(id, marker, true);
    // The product captures completed keyboard/pointer selections, not selectionchange alone.
    await page.keyboard.press("Shift");
    const quote = page.getByRole("button", { name: "引用", exact: true });
    await quote.waitFor(); await quote.click();
    const references = page.getByRole("button", { name: "查看 1 个已选文本", exact: true });
    await references.waitFor(); await references.hover();
    const source = page.getByRole("button").filter({ hasText: marker });
    requireCondition(await source.count() === 1, "quote_source_button_not_unique");
    await source.click();
    await showMarker(id, marker, "15-reference-source");
    await references.hover();
    await page.getByRole("button", { name: "清空全部引用", exact: true }).click();
    await references.waitFor({ state: "hidden" });
    return { session_id: fixtures.short.session_id, source_uuid: id, selection: "scoped_dom_range",
      scope: "draft_quote_and_loaded_source_navigation", sent: false };
  }, true);
  await Promise.allSettled([...pendingNetwork]);
  summarizeHistoryResponseBudget();
  requireCondition(report.history_response_budget.exceeded_count === 0, "history_response_byte_budget_exceeded");
  requireCondition(report.prohibited_request_count === 0, "unexpected_mutating_product_request");
  report.status = report.failures.length ? "failed"
    : report.steps.some((item) => item.status === "not_verified") ? "passed_with_gaps" : "passed";
} catch (error) {
  report.status = "failed";
  report.failure_code = error.probeCode ?? "probe_setup_or_connection_failed";
  report.failure_type = error.name;
} finally {
  // One bounded recovery attempt if the normal per-copy finally could not restore.
  if (!await restoreClipboard()) {
    report.status = "failed";
    report.failure_code = "clipboard_restore_failed";
  }
  await Promise.allSettled([...pendingNetwork]);
  summarizeHistoryResponseBudget();
  if (page && identityConfirmed && report.status === "failed") {
    try { await screenshot("failure-final-state"); } catch { /* No raw error output. */ }
  }
  report.finished_at = new Date().toISOString();
  await save();
  // For connectOverCDP this disconnects the client; the caller retains its runtime.
  if (browser) await browser.close();
}
console.log(JSON.stringify({ status: report.status, output, steps: report.steps.length,
  failures: report.failures, requests: report.requests.length }));
process.exitCode = report.status === "failed" ? 1 : 0;
