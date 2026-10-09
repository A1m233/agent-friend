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
  if (url.pathname === "/v1/sessions/search") return "search";
  if (/^\/v1\/sessions\/[^/]+\/history(?:\/content)?$/.test(url.pathname)) return "removed_history_api";
  if (!/^\/v1\/sessions\/[a-f0-9-]{36}$/.test(url.pathname)) return null;
  return url.searchParams.get("view") === "active" && url.searchParams.get("turn_limit") === "30"
    ? "history" : "unbounded_session_read";
}
if (process.argv.slice(2).join() === "--self-test-url-classification") {
  const sid = "00000000-0000-0000-0000-000000000001";
  const cases = [
    [`/v1/sessions/${sid}?view=active&turn_limit=30`, "history"],
    [`/v1/sessions/${sid}?view=active&turn_limit=30&before=x&history_epoch=e`, "history"],
    [`/v1/sessions/${sid}?view=active`, "unbounded_session_read"],
    [`/v1/sessions/${sid}`, "unbounded_session_read"],
    [`/v1/sessions/${sid}/history`, "removed_history_api"],
    [`/v1/sessions/${sid}/history/content?ref=x`, "removed_history_api"],
    ["/v1/sessions/search?q=history", "search"],
    ["/src/stores/historyProjection.ts", null], ["/src/stores/sessionHistoryPages.ts?t=1", null],
    [`/v1/sessions/${sid}/approval-mode`, null], ["/v1/sessions", null],
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
if (options["--focus"] && !["long-tools", "search", "references"].includes(options["--focus"])) throw new Error("invalid_probe_focus");
const cdp = new URL(options["--cdp"]);
if (cdp.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(cdp.hostname)) {
  throw new Error("probe_requires_local_cdp");
}
const identityBytes = await readFile(resolve(options["--identity"]));
const identity = JSON.parse(identityBytes.toString("utf8"));
const expectedKinds = ["long", "short"];
if (identity.schema_version !== 2 || identity.kind !== "isolated_desktop_turn_history_fixture"
  || identity.synthetic_only !== true || identity.sessions?.length !== 2
  || identity.sessions.map((s) => s.kind).sort().join() !== expectedKinds.join()) {
  throw new Error("invalid_fixture_identity");
}
const fixtures = Object.fromEntries(identity.sessions.map((s) => [s.kind, s]));
if (fixtures.long.expected.latest.references?.map((reference) => reference.source_role).join() !== "user,assistant") {
  throw new Error("fixture_requires_persisted_oldest_references");
}
const expectedIds = identity.sessions.map((s) => s.session_id).sort();
if (new Set(expectedIds).size !== 2 || expectedIds.some((id) => !/^[a-f0-9-]{36}$/.test(id))) {
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
{ cwd: root, encoding: "utf8" }).split("\0").filter(Boolean)
  .filter((path) => !/^frontend\/src\/.*\.(test|spec)\.[cm]?[tj]sx?$/.test(path));
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
  schema_version: 2, kind: "desktop_complete_turn_history_ui_probe", status: "running", started_at: new Date().toISOString(),
  identity_sha256: hash(identityBytes), script_sha256: hash(await readFile(fileURLToPath(import.meta.url))),
  runtime: { worktree_sha256: hash(normalizedPath(root)), branch_sha256: hash(branch), owner_pid: lease.ownerPid,
    started_at: lease.startedAt, source_newest_at: new Date(sourceNewest).toISOString(), cdp_port: Number(cdp.port),
    freshness_scope: "runtime_source_excluding_frontend_test_and_spec_modules" },
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
let searchObservationActive = false;
const pendingNetwork = new Set();
const starts = new WeakMap();
function summarizeHistoryResponseBudget() {
  const pages = report.requests.filter((request) => request.kind === "history" && request.status >= 200 && request.status < 300);
  report.history_response_budget = { turn_limit: 30, checked_count: pages.length,
    largest_response_bytes: Math.max(0, ...pages.map((request) => request.bytes)),
    exceeded_count: pages.filter((request) => request.turn_count > 30).length,
    measurement: "complete_turns_with_unabridged_event_bodies_no_byte_truncation" };
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
      user_count: state.messages.filter((m) => m.role === "user").length,
      assistant_count: state.messages.filter((m) => m.role === "assistant").length,
      tools_per_message: state.messages.filter((m) => m.blocks.some((b) => b.kind === "tool"))
        .map((m) => ({ message_id: m.id, tool_count: m.blocks.filter((b) => b.kind === "tool").length })),
      has_before: state.hasOlderHistory(),
      calibration_phase: state.runCalibrationPhase,
      search_target: state.searchTarget && { token: state.searchTarget.token, session_id: state.searchTarget.sessionId,
        anchor_event_uuid: state.searchTarget.anchorEventUuid, loading: state.searchTarget.loading },
      active_run_count: Object.values(state.runSnapshots).filter((r) => r.status !== "terminal").length,
      active_run_ids: Object.values(state.runSnapshots).filter((r) => r.status !== "terminal").map((r) => r.run_id),
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
async function beginSearchObservation() {
  await page.evaluate(async () => {
    const store = (await import("/src/stores/conversation.ts")).useConversationStore;
    const started = performance.now();
    const capture = { frames: [], dropped: 0, unsubscribe: null };
    let previous;
    const record = (state) => {
      const values = { session_id: state.currentSessionId, history_loading: state.historyLoading,
        calibration_phase: state.runCalibrationPhase, message_count: state.messages.length,
        has_older: state.hasOlderHistory(), search_target: state.searchTarget && {
          token: state.searchTarget.token, loading: state.searchTarget.loading,
          session_id: state.searchTarget.sessionId, anchor_event_uuid: state.searchTarget.anchorEventUuid,
        } };
      const signature = JSON.stringify(values);
      if (signature === previous) return;
      previous = signature;
      if (capture.frames.length >= 512) { capture.dropped++; return; }
      capture.frames.push({ elapsed_ms: performance.now() - started, ...values });
    };
    record(store.getState());
    capture.unsubscribe = store.subscribe(record);
    window.__historyProbeSearchObservation = capture;
  });
  searchObservationActive = true;
}
async function endSearchObservation() {
  if (!searchObservationActive) return undefined;
  searchObservationActive = false;
  return page.evaluate(() => {
    const capture = window.__historyProbeSearchObservation;
    if (!capture) return undefined;
    capture.unsubscribe();
    delete window.__historyProbeSearchObservation;
    return { frames: capture.frames, dropped: capture.dropped };
  });
}
async function step(name, callback, optional = false) {
  if (options["--focus"] === "long-tools" && !name.startsWith("long_")) return;
  if (options["--focus"] === "search" && !name.startsWith("search_")) return;
  if (options["--focus"] === "references" && !name.startsWith("reference_")) return;
  const began = Date.now();
  const entry = { name, status: "running" };
  report.steps.push(entry);
  await save();
  try { entry.result = await callback(); await assertOriginalHistoryUi(); entry.status = "passed"; }
  catch (error) {
    const fatal = error.probeCode === "clipboard_restore_failed";
    entry.status = optional && !fatal ? "not_verified" : "failed";
    entry.error_code = error.probeCode ?? "ui_operation_failed";
    entry.error_type = error.name;
    if (error.geometry) entry.geometry = error.geometry;
    try { entry.failure_state = await snapshot(); } catch { /* Page may already be unavailable. */ }
    if (identityConfirmed) { try { await screenshot(`failure-${name}`); } catch { /* Retain scoped diagnostics. */ } }
    if (!optional || fatal) report.failures.push(name);
    if (fatal) throw error; // Do not reload the page and discard its only clipboard backup.
  } finally {
    if (searchObservationActive) entry.state_sequence = await endSearchObservation();
    entry.duration_ms = Date.now() - began; await save();
  }
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
  const point = box?.point ?? { x: bounds.x + Math.min(box ? 12 : 4, bounds.width / 4),
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
    const clips = [];
    while (parent) {
      const style = getComputedStyle(parent);
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
        const box = parent.getBoundingClientRect();
        clips.push({ box, element: parent });
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
    function deepHit(x, y) {
      let current = document.elementFromPoint(x, y);
      while (current?.shadowRoot) {
        const inner = current.shadowRoot.elementFromPoint(x, y);
        if (!inner || inner === current) break;
        current = inner;
      }
      return current;
    }
    const centerHit = deepHit(rect.left + rect.width / 2, rect.top + rect.height / 2);
    const textParent = hit.node.parentElement;
    const unobscured = !!centerHit && (textParent === centerHit || textParent?.contains(centerHit));
    let target;
    for (const candidate of scrollables) {
      let visible = { top: Math.max(0, candidate.y), bottom: Math.min(innerHeight, candidate.y + candidate.height),
        left: Math.max(0, candidate.x), right: Math.min(innerWidth, candidate.x + candidate.width) };
      for (const ancestor of clips) {
        if (ancestor.element !== candidate.element && ancestor.element.contains(candidate.element)) {
          visible = { top: Math.max(visible.top, ancestor.box.top), bottom: Math.min(visible.bottom, ancestor.box.bottom),
            left: Math.max(visible.left, ancestor.box.left), right: Math.min(visible.right, ancestor.box.right) };
        }
      }
      if (visible.bottom <= visible.top || visible.right <= visible.left) continue;
      const delta = rect.top + rect.height / 2 - (visible.top + visible.bottom) / 2;
      if (delta < 0 ? candidate.scroll_top <= 0.5 : candidate.scroll_top >= candidate.max_scroll_top - 0.5) continue;
      // The left edge of a parent group is outside its child output scrollers.
      const point = { x: visible.left + Math.min(4, (visible.right - visible.left) / 4),
        y: visible.top + Math.min(24, (visible.bottom - visible.top) / 4) };
      const atPoint = deepHit(point.x, point.y);
      if (!atPoint || !candidate.element.contains(atPoint)) continue;
      let nearest = atPoint;
      while (nearest && !(nearest.scrollHeight > nearest.clientHeight + 1
        && /(auto|scroll)/.test(getComputedStyle(nearest).overflowY))) {
        nearest = nearest.parentElement ?? nearest.getRootNode()?.host;
      }
      if (nearest !== candidate.element) continue;
      target = { x: visible.left, y: visible.top, width: visible.right - visible.left, height: visible.bottom - visible.top,
        point, delta, scroll_top: candidate.scroll_top, max_scroll_top: candidate.max_scroll_top };
      break;
    }
    const targetReachable = !!target;
    const targetBox = target;
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
    } else await wheel(Math.max(-20000, Math.min(20000, target?.delta ?? delta)), target);
  }
  const error = new Error("marker_not_visible_in_viewport");
  error.probeCode = "marker_not_visible_in_viewport";
  error.geometry = lastGeometry;
  throw error;
}
async function assertOriginalHistoryUi() {
  for (const name of ["加载完整内容", "重新加载内容", "加载较新的消息"]) {
    requireCondition(await page.getByRole("button", { name, exact: true }).count() === 0, "unexpected_per_item_history_control");
  }
}
async function toolGroup() {
  const turn = fixtures.long.expected.latest;
  const message = row(turn.message_uuid);
  await findRow(turn.message_uuid);
  const run = message.getByRole("button", { name: "展开执行过程", exact: true });
  if (await run.count()) await run.click();
  const group = message.getByRole("button", { name: new RegExp(`^执行了 ${turn.tool_count} 个步骤`) });
  await group.waitFor({ state: "visible" });
  if (await group.getAttribute("aria-expanded") !== "true") await group.click();
  return message.locator('[data-slot="collapsible-trigger"]').filter({ hasText: "运行本地命令" });
}
async function inspectTool(index, name) {
  const turn = fixtures.long.expected.latest;
  const triggers = await toolGroup();
  requireCondition(await triggers.count() === turn.tool_count, "tool_group_item_count_mismatch");
  const trigger = triggers.nth(index);
  if (await trigger.getAttribute("aria-expanded") !== "true") await trigger.click();
  const tool = turn.tools[index];
  const begin = await showMarker(turn.message_uuid, `${tool.marker}_BEGIN`, `${name}-begin`);
  const end = await showMarker(turn.message_uuid, `${tool.marker}_END`, `${name}-end`);
  await trigger.click();
  return { request_uuid: tool.request_uuid, result_uuid: tool.result_uuid, begin, end };
}
async function copyAnswer(fixture) {
  const id = fixture.expected.latest.message_uuid;
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
    requireCondition(clipboard.sha256 === fixture.expected.latest.answer_sha256, "complete_message_copy_hash_mismatch");
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
      const body = response ? await response.body() : null;
      const bytes = body?.length ?? 0;
      const kind = observedRequestKind(request.url());
      let turnCount;
      const searchAnchors = kind === "search" && response?.ok()
        ? JSON.parse(body.toString("utf8")).message_results?.map((result) => ({ session_id: result.session_id,
          anchor_event_uuid: result.anchor_event_uuid })) : undefined;
      if (kind === "history" && response?.ok()) {
        const payload = JSON.parse(body.toString("utf8"));
        if (!Array.isArray(payload.active_events) || payload.active_events.some((event) => "body" in event || "result_event" in event)) {
          report.failures.push("invalid_complete_turn_response");
        } else turnCount = payload.active_events.filter((event) => ["user_message", "system_trigger"].includes(event.type)).length;
      }
      report.requests.push({ kind, turn_count: turnCount, search_anchors: searchAnchors,
      session_id: kind === "history" ? url.pathname.split("/").at(-1) : undefined,
      direction: url.searchParams.has("before") ? "before" : "latest",
      epoch_supplied: url.searchParams.has("history_epoch"), status: response?.status(), bytes,
      duration_ms: Date.now() - starts.get(request), request_id: headers["x-agent-friend-request-id"] ?? null });
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

  if (options["--focus"] === "search") await openFixture(fixtures.short);
  const short = fixtures.short.expected.latest;
  const long = fixtures.long.expected.latest;
  await step("short_latest_visible", async () => {
    const state = await openFixture(fixtures.short);
    requireCondition(state.user_count === 2 && state.assistant_count === 2, "short_turn_projection_changed");
    return { state, end: await showMarker(short.message_uuid, `${short.answer_marker}_END`, "01-short") };
  });
  await step("short_copy", () => copyAnswer(fixtures.short), true);
  await step("restore_after_webview_reload", async () => {
    await page.reload({ waitUntil: "domcontentloaded" });
    const state = await waitReady(fixtures.short.session_id);
    return { state, end: await showMarker(short.message_uuid, `${short.answer_marker}_END`, "02-restore"),
      scope: "webview_reload_not_process_restart" };
  });
  await step("long_latest_30_turns", async () => {
    const state = await openFixture(fixtures.long);
    requireCondition(state.user_count === 30 && state.assistant_count === 30 && state.has_before, "latest_not_30_complete_turns");
    requireCondition(state.tools_per_message.length === 1 && state.tools_per_message[0].tool_count === long.tool_count,
      "tool_run_not_aggregated_into_one_message");
    const begin = await showMarker(long.message_uuid, `${long.answer_marker}_BEGIN`, "03-long-answer-begin");
    const end = await showMarker(long.message_uuid, `${long.answer_marker}_END`, "04-long-answer-end");
    return { state, begin, end };
  });
  await step("long_first_grouped_tool", () => inspectTool(0, "05-first-tool"));
  await step("long_last_grouped_tool", () => inspectTool(long.tool_count - 1, "06-last-tool"));
  await step("long_copy", () => copyAnswer(fixtures.long), true);
  await step("long_three_pages_by_scroll", async () => {
    const before = await snapshot();
    for (let attempt = 0; attempt < 35 && (await snapshot()).has_before; attempt++) await wheel(-3000);
    const after = await snapshot();
    requireCondition(!after.has_before && after.user_count === 65 && after.assistant_count === 65, "three_pages_incomplete");
    requireCondition(new Set(after.message_ids).size === after.message_count, "duplicate_message_rows");
    requireCondition(fixtures.long.expected.turns.every((turn) => after.message_ids.includes(turn.user_uuid)
      && after.message_ids.includes(turn.message_uuid)), "turn_boundary_missing");
    await findRow(fixtures.long.expected.oldest.user_uuid, -1);
    const marker = await showMarker(fixtures.long.expected.oldest.user_uuid, fixtures.long.expected.oldest.user_marker, "07-oldest-turn");
    return { before, after, marker };
  });
  await step("switch_to_short", async () => {
    const state = await openFixture(fixtures.short);
    return { state, end: await showMarker(short.message_uuid, `${short.answer_marker}_END`, "08-switched-short") };
  });
  await step("search_older_turn_uses_pagination", async () => {
    // Reload clears the in-memory long page cache so the old target must be paged in.
    await page.reload({ waitUntil: "domcontentloaded" }); await waitReady(fixtures.short.session_id);
    const started = report.requests.length;
    const target = fixtures.long.expected.oldest;
    await page.keyboard.press(process.platform === "darwin" ? "Meta+Shift+f" : "Control+Shift+f");
    const dialog = page.getByRole("dialog", { name: "搜索全部对话", exact: true });
    await dialog.waitFor(); await dialog.getByLabel("搜索全部对话", { exact: true }).fill(target.user_marker);
    const option = dialog.getByRole("option").filter({ hasText: target.user_marker });
    await option.first().waitFor({ timeout: 45000 });
    requireCondition(await option.count() === 1, "search_result_not_unique");
    await beginSearchObservation();
    await option.click(); await dialog.waitFor({ state: "hidden" });
    await waitReady(fixtures.long.session_id);
    await row(target.user_uuid).waitFor({ state: "attached", timeout: 45000 });
    const marker = await showMarker(target.user_uuid, target.user_marker, "09-search-oldest");
    await Promise.allSettled([...pendingNetwork]);
    requireCondition(report.requests.slice(started).filter((r) => r.kind === "history" && r.direction === "before").length >= 2,
      "search_did_not_page_to_oldest_turn");
    return { marker, state: await snapshot() };
  });
  await step("quote_and_source_navigation", async () => {
    await openFixture(fixtures.short);
    const id = short.message_uuid, marker = `${short.answer_marker}_BEGIN`;
    await showMarker(id, marker); await markerGeometry(id, marker, true); await page.keyboard.press("Shift");
    await page.getByRole("button", { name: "引用", exact: true }).click();
    const references = page.getByRole("button", { name: "查看 1 个已选文本", exact: true });
    await references.waitFor(); await references.hover();
    const source = page.getByRole("button").filter({ hasText: marker });
    requireCondition(await source.count() === 1, "quote_source_not_unique");
    await source.click(); await showMarker(id, marker, "10-reference-source");
    await references.hover(); await page.getByRole("button", { name: "清空全部引用", exact: true }).click();
    await references.waitFor({ state: "hidden" });
    return { source_uuid: id, sent: false, selection: "scoped_dom_range" };
  });
  for (const reference of long.references) {
    await step(`reference_unloaded_${reference.source_role}_uses_pagination`, async () => {
      // Start from another session before reloading: no long-session cache or
      // previously mounted source can satisfy the reference navigation.
      await openFixture(fixtures.short);
      await page.reload({ waitUntil: "domcontentloaded" });
      await waitReady(fixtures.short.session_id);
      await openFixture(fixtures.long);
      await findRow(long.user_uuid, -1);
      const before = await snapshot();
      requireCondition(before.user_count === 30 && before.assistant_count === 30 && before.has_before,
        "reference_initial_page_not_30_turns");
      requireCondition(!before.message_ids.includes(reference.source_uuid), "reference_source_already_loaded");
      await Promise.allSettled([...pendingNetwork]);
      const started = report.requests.length;
      const summary = row(long.user_uuid).getByRole("button", { name: "查看 2 个已选文本", exact: true });
      await summary.hover();
      const source = page.getByRole("button").filter({ hasText: reference.marker });
      await source.first().waitFor({ state: "visible" });
      requireCondition(await source.count() === 1, "persisted_reference_source_not_unique");
      await source.click();
      await row(reference.source_uuid).waitFor({ state: "attached", timeout: 45000 });
      // Observe the product's own source navigation without scrolling it into place.
      let marker;
      for (let attempt = 0; attempt < 40; attempt++) {
        marker = await markerGeometry(reference.source_uuid, reference.marker);
        if (marker.visible) break;
        await page.waitForTimeout(100);
      }
      requireCondition(marker?.visible, "persisted_reference_source_not_visible");
      const after = await snapshot();
      requireCondition(after.user_count === 65 && after.assistant_count === 65 && !after.has_before,
        "reference_older_turns_incomplete");
      await Promise.allSettled([...pendingNetwork]);
      const reads = report.requests.slice(started);
      const pages = reads.filter((request) => request.kind === "history"
        && request.session_id === fixtures.long.session_id && request.direction === "before");
      requireCondition(pages.map((request) => request.turn_count).join() === "30,5"
        && pages.every((request) => request.epoch_supplied && request.status === 200),
      "reference_did_not_page_30_then_5_turns");
      requireCondition(!reads.some((request) => ["removed_history_api", "unbounded_session_read"].includes(request.kind)),
        "reference_used_history_or_getFull");
      await screenshot(`11-reference-unloaded-${reference.source_role}`);
      return { reference_id: reference.reference_id, source_uuid: reference.source_uuid, before, after,
        page_turn_counts: pages.map((request) => request.turn_count), marker_sha256: hash(reference.marker), marker };
    });
  }
  await Promise.allSettled([...pendingNetwork]);
  summarizeHistoryResponseBudget();
  requireCondition(report.history_response_budget.exceeded_count === 0, "history_response_turn_limit_exceeded");
  requireCondition(!report.requests.some((r) => ["removed_history_api", "unbounded_session_read"].includes(r.kind)), "unexpected_history_or_getFull_request");
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
