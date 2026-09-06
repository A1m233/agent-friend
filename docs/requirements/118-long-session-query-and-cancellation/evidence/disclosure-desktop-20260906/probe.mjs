// One-off 118 UI probe. Run only after the delivery agent validates the Tauri runtime.
// Usage: node probe.mjs <repo-root> <new-output-directory> <loopback-CDP-endpoint>
// Does not seed sessions, submit input, invoke tools, or test persistence / model behavior.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const [repoArg, outputArg, cdpArg] = process.argv.slice(2);
assert.ok(repoArg && outputArg && cdpArg, "Expected repo root, new output directory and CDP endpoint");
const repo = resolve(repoArg);
const output = resolve(outputArg);
const cdp = new URL(cdpArg);
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(cdp.hostname), "CDP must be loopback");
assert.equal(cdp.protocol, "http:");
assert.equal(process.platform, "win32", "This probe binds the Windows dev lease / WebView2");
const lease = JSON.parse(await readFile(resolve(process.env.LOCALAPPDATA,
  "agent-friend/dev-runtime/lease.json"), "utf8"));
const comparable = (value) => resolve(value).replaceAll("\\", "/").toLowerCase();
assert.equal(comparable(lease.worktree), comparable(repo), "Refusing a different worktree's runtime");
assert.equal(lease.branch, "feature/118-long-session-query-and-cancellation");
assert.equal(lease.mode, "tauri");
assert.ok(lease.ports.includes(Number(cdp.port || 80)), "CDP must belong to the verified lease");
const require = createRequire(resolve(repo, "frontend/package.json"));
const { chromium } = require("playwright");
await mkdir(output, { recursive: false });
const report = {
  synthetic: true,
  llmCalls: 0,
  surface: "desktop-webview-ui-only",
  passed: false,
  restored: false,
  checks: [],
  screenshots: [],
  runtime: { worktree: lease.worktree, branch: lease.branch, mode: lease.mode,
    ownerPid: lease.ownerPid, startedAt: lease.startedAt, cdp: cdp.href },
  limitations: ["Synthetic in-memory messages and lifecycle facts; no LLM or tool execution",
    "Screenshots capture the real Tauri WebView viewport, not the native title bar",
    "Search target injection verifies navigation UI only, not backend search",
    "Copy checks the real UI handler payload at a mocked navigator.clipboard.writeText boundary; the system clipboard is neither read nor written",
    "Presence heartbeats, voice prewarm, and the synthetic session runtime lookup are answered locally by the probe",
    "Only exact Tauri log, event-emit and read-only desktop presence IPC URLs are allowed; this does not test the IPC transport",
    "Does not verify persistence, reload restoration, or real K3 behavior"],
};
const fixture = `synthetic-disclosure-118-${randomUUID()}`;
const backupKey = `__disclosure118_${randomUUID().replaceAll("-", "")}`;
let browser;
let page;
let staged = false;
let routeInstalled = false;
const blockedWrites = [];
let suppressedPresenceHeartbeats = 0;
let suppressedFixtureRuntimeReads = 0;
let suppressedVoicePrewarms = 0;
let voicePrewarmUrl;
const allowedTauriIpcUrls = new Set();
const allowedTauriIpcRequests = new Map();
const guardWrites = async (route) => {
  const request = route.request();
  const requestUrl = new URL(request.url());
  const pathname = requestUrl.pathname;
  // On Windows, Tauri serializes these normal shell commands as POSTs through
  // its IPC custom protocol. Continue them unmodified; never fake event delivery.
  if (request.method() === "POST" && allowedTauriIpcUrls.has(request.url())) {
    const previous = allowedTauriIpcRequests.get(request.url());
    allowedTauriIpcRequests.set(request.url(), { method: "POST", origin: requestUrl.origin,
      pathname, count: (previous?.count ?? 0) + 1 });
    return route.continue();
  }
  // Composer focus may initiate connect-only ASR warmup. Voice is outside this
  // probe; return the real API's disabled result shape without contacting ASR.
  if (request.method() === "POST" && request.url() === voicePrewarmUrl) {
    suppressedVoicePrewarms += 1;
    const payload = request.postDataJSON();
    const traceId = typeof payload?.traceId === "string" ? payload.traceId : `${fixture}:voice-prewarm`;
    return route.fulfill({ status: 200, contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": new URL(page.url()).origin, Vary: "Origin" },
      body: JSON.stringify({ status: "disabled", traceId, ttlMs: 0,
        message: "Voice prewarm is disabled for this synthetic disclosure UI probe" }) });
  }
  // The desktop reports presence every five seconds and on focus changes. Keep
  // this synthetic session out of the real runtime presence state.
  if (request.method() === "POST" && pathname === "/v1/runtime/presence/desktop") {
    suppressedPresenceHeartbeats += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  }
  // SessionSync reacts to currentSessionId even for direct store injection.
  // A missing synthetic session must not reach the backend or recalibrate runs.
  if (request.method() === "GET"
    && pathname === `/v1/sessions/${encodeURIComponent(fixture)}/runtime-snapshot`) {
    suppressedFixtureRuntimeReads += 1;
    return route.fulfill({ status: 404, contentType: "application/json",
      body: JSON.stringify({ error: { code: "synthetic_ui_only", message: "Synthetic UI fixture has no backend session" } }) });
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
    blockedWrites.push({ method: request.method(), origin: requestUrl.origin, pathname });
    return route.abort("blockedbyclient");
  }
  return route.continue();
};
const startedAt = new Date(Date.now() - 8000).toISOString();
const finishedAt = new Date().toISOString();
const text = (id, value, phase = "unknown") => ({ kind: "text", mid: id, text: value, phase });
const tool = (id) => ({ kind: "tool", toolCallId: id, name: "calculate",
  args: JSON.stringify({ expression: "17 * 23" }), result: "391", status: "done" });
const message = (name, overrides = {}) => {
  const id = `${fixture}:${name}`;
  return { id, runId: id, role: "assistant", status: "complete", createdAt: startedAt,
    runStartedAt: startedAt, runFinishedAt: finishedAt, runOutcome: "completed",
    assistantPhase: { mode: "legacy", validation: "missing_final" },
    blocks: [text(`${id}:before`, `${name} 合成过程：先检查相关信息。`), tool(`${id}:tool`),
      text(`${id}:final`, `${name} 合成最终回答：结果为 391。`)], ...overrides };
};
const stage = async (value) => {
  await page.evaluate(async ({ fixture, value }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const state = useConversationStore.getState();
    const run = { run_id: value.runId, session_id: fixture,
      status: value.status === "streaming" ? "running" : "terminal",
      outcome: value.runOutcome ?? null, reason_code: "", channel: "text",
      origin: "synthetic-desktop-probe", control_scope: "desktop_text",
      created_at: value.createdAt, started_at: value.runStartedAt ?? null,
      finished_at: value.runFinishedAt ?? null, version: 1 };
    // Directly stage view facts; do not call actions that fetch or write backend state.
    useConversationStore.setState({ currentSessionId: fixture, messages: [value],
      historyLoading: false, searchTarget: null, editingMessage: null,
      runCalibrationPhase: "ready", runSnapshots: { [value.runId]: run },
      sessionViews: { ...state.sessionViews, [fixture]: { ...state.sessionViews[fixture], messages: [value] } } });
  }, { fixture, value });
  const row = page.locator(`[data-message-id="${value.id}"]`);
  await row.waitFor({ state: "visible", timeout: 10000 });
  await row.scrollIntoViewIfNeeded();
  return row;
};
const snapshot = async (name) => {
  const path = resolve(output, `${name}.png`);
  await page.screenshot({ path });
  report.screenshots.push({ name, path, surface: "desktop-webview-viewport" });
};
const source = (row, expected) => row.locator(`[data-execution-disclosure-source="${expected}"]`)
  .waitFor({ state: "visible", timeout: 5000 });
const processText = (row, name) => row.getByText(`${name} 合成过程：先检查相关信息。`, { exact: true });
const answerText = (row, name) => row.getByText(`${name} 合成最终回答：结果为 391。`, { exact: true });
const toggle = (row, open) => row.getByRole("button", { name: open ? "收起执行过程" : "展开执行过程", exact: true });
try {
  browser = await chromium.connectOverCDP(cdp.href, { timeout: 10000 });
  const candidates = browser.contexts().flatMap((context) => context.pages()).filter((candidate) => {
    try { const url = new URL(candidate.url()); return url.pathname === "/chat.html"
      && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname); } catch { return false; }
  });
  assert.equal(candidates.length, 1, "Expected exactly one loopback chat WebView on this CDP endpoint");
  [page] = candidates;
  page.setDefaultTimeout(8000);
  report.url = page.url();
  report.viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight,
    tauri: Boolean(window.__TAURI_INTERNALS__) }));
  assert.equal(report.viewport.tauri, true, "Must be a real Tauri WebView");
  const probeEndpoints = await page.evaluate(async () => {
    const { localServiceAbsoluteBaseUrl } = await import("/src/constants/index.ts");
    return {
      ipc: ["plugin:log|log", "plugin:event|emit", "desktop_presence_probe"].map((command) =>
        window.__TAURI_INTERNALS__.convertFileSrc(command, "ipc")),
      voicePrewarm: new URL("/voice/transcriptions/prewarm", localServiceAbsoluteBaseUrl("voice")).href,
    };
  });
  for (const url of probeEndpoints.ipc) {
    const parsed = new URL(url);
    assert.equal(parsed.hostname, "ipc.localhost", "Expected the Windows Tauri IPC source");
    assert.ok(["http:", "https:"].includes(parsed.protocol));
    assert.ok(["/plugin%3Alog%7Clog", "/plugin%3Aevent%7Cemit", "/desktop_presence_probe"].includes(parsed.pathname));
    allowedTauriIpcUrls.add(parsed.href);
  }
  voicePrewarmUrl = probeEndpoints.voicePrewarm;
  assert.equal(new URL(voicePrewarmUrl).origin, "http://127.0.0.1:18900");
  report.transportBoundary = { allowedTauriIpcUrls: [...allowedTauriIpcUrls], suppressedVoicePrewarmUrl: voicePrewarmUrl };
  // Fail before injecting if this runtime has not loaded the new policy implementation.
  await page.evaluate(async (sample) => {
    const { executionDisclosurePolicy } = await import("/src/pages/chat/components/executionDisclosurePolicy.ts");
    if (executionDisclosurePolicy(sample).source !== "inferred") throw new Error("118 fallback source is not loaded");
  }, message("identity-check"));
  await page.route("**/*", guardWrites);
  routeInstalled = true;
  await page.evaluate(async ({ backupKey, fixture }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const { useDraftStore } = await import("/src/stores/drafts.ts");
    const state = useConversationStore.getState();
    if (state.historyLoading || state.messages.some((entry) => entry.status === "streaming")
      || Object.values(state.runSnapshots).some((run) => run.status !== "terminal")) {
      throw new Error("Refusing to replace a busy or loading conversation view");
    }
    const keys = ["currentSessionId", "messages", "historyLoading", "searchTarget", "editingMessage",
      "runCalibrationPhase", "runSnapshots", "sessionViews"];
    window[backupKey] = { fields: Object.fromEntries(keys.map((key) => [key, state[key]])),
      fixtureDraftExisted: Object.hasOwn(useDraftStore.getState().workspaces, fixture),
      fixtureDraft: useDraftStore.getState().workspaces[fixture],
      scrollTop: document.querySelector(".chat-message-viewport")?.scrollTop ?? 0 };
  }, { backupKey, fixture });
  staged = true;

  const inferred = message("inferred");
  let row = await stage(inferred);
  await source(row, "inferred");
  await toggle(row, false).waitFor({ state: "visible" });
  await processText(row, "inferred").waitFor({ state: "hidden" });
  await answerText(row, "inferred").waitFor({ state: "visible" });
  assert.equal(await row.getByRole("button", { name: /计算/ }).count(), 0);
  await snapshot("inferred-collapsed");
  await page.evaluate((backupKey) => {
    const backup = window[backupKey];
    if (!backup) throw new Error("Missing view backup before installing copy capture");
    const clipboard = navigator.clipboard;
    if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("Clipboard write boundary is unavailable");
    // Save the method descriptor only. Never call clipboard.readText or the real writeText.
    backup.clipboard = clipboard;
    backup.clipboardWriteDescriptor = Object.getOwnPropertyDescriptor(clipboard, "writeText");
    backup.clipboardWrites = [];
    Object.defineProperty(clipboard, "writeText", { configurable: true, writable: true,
      value: async (value) => { backup.clipboardWrites.push(String(value)); } });
    backup.clipboardCaptureInstalled = true;
  }, backupKey);
  await row.getByRole("button", { name: "复制消息", exact: true }).click();
  await row.getByRole("button", { name: "已复制", exact: true }).waitFor({ state: "visible" });
  const capturedCopies = await page.evaluate((backupKey) => window[backupKey].clipboardWrites, backupKey);
  assert.deepEqual(capturedCopies, ["inferred 合成过程：先检查相关信息。\n\ninferred 合成最终回答：结果为 391。"]);
  await processText(row, "inferred").waitFor({ state: "hidden" });
  report.copy = { boundary: "navigator.clipboard.writeText", systemClipboardRead: false,
    systemClipboardWritten: false, payloads: capturedCopies };
  await snapshot("inferred-collapsed-copy-payload");
  report.checks.push("Real copy button on a collapsed inferred message passes both hidden process and final text to the captured clipboard-write boundary; system clipboard untouched");
  await toggle(row, false).click();
  await processText(row, "inferred").waitFor({ state: "visible" });
  await row.getByRole("button", { name: /计算/ }).waitFor({ state: "visible" });
  await answerText(row, "inferred").waitFor({ state: "visible" });
  await snapshot("inferred-expanded");
  const quoteSource = processText(row, "inferred");
  await quoteSource.scrollIntoViewIfNeeded();
  await page.mouse.move(5, 5);
  await quoteSource.evaluate((element) => {
    const selection = window.getSelection();
    if (!selection) throw new Error("Selection API unavailable");
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    window.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  });
  const quoteButton = page.getByRole("button", { name: "引用", exact: true });
  await quoteButton.waitFor({ state: "visible" });
  report.quoteActionGeometry = await quoteButton.evaluate((button) => {
    const rect = button.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height,
      hitInsideButton: Boolean(hit && button.contains(hit)),
      pointerEvents: getComputedStyle(button).pointerEvents,
      bodyPointerEvents: getComputedStyle(document.body).pointerEvents };
  });
  assert.equal(report.quoteActionGeometry.hitInsideButton, true, "Quote control must be pointer-reachable");
  assert.notEqual(report.quoteActionGeometry.pointerEvents, "none");
  await snapshot("inferred-quote-action");
  await quoteButton.click();
  await quoteButton.waitFor({ state: "hidden" });
  report.quote = await page.evaluate(async ({ fixture, mid }) => {
    const { useDraftStore } = await import("/src/stores/drafts.ts");
    return useDraftStore.getState().workspaces[fixture]?.references.find((reference) =>
      reference.source.textBlockId === mid) ?? null;
  }, { fixture, mid: `${inferred.id}:before` });
  assert.ok(report.quote?.referenceId);
  assert.equal(report.quote.quotedText, "inferred 合成过程：先检查相关信息。");
  assert.equal(report.quote.sourceRole, "assistant");
  assert.equal(report.quote.availability, "available");
  assert.deepEqual(report.quote.source, { kind: "session_message", messageEventUuid: inferred.id,
    textBlockId: `${inferred.id}:before`, externalSourceKey: null });
  assert.equal(report.quote.locator.kind, "text_range");
  assert.equal(report.quote.locator.coordinateSpace, "rendered_text_utf16_v1");
  assert.equal(report.quote.locator.start, 0);
  assert.equal(report.quote.locator.end, report.quote.quotedText.length);
  await snapshot("inferred-quoted-original-mid");
  report.checks.push("DOM Range selects expanded process text and a real pointer click on Quote preserves its original message id, text mid, quoted text and UTF-16 locator in the synthetic draft");
  await toggle(row, true).click();
  await processText(row, "inferred").waitFor({ state: "hidden" });
  await answerText(row, "inferred").waitFor({ state: "visible" });
  report.checks.push("Inferred successful completion collapses process text and tools, preserves final text; real clicks expand and collapse");

  await page.evaluate(async ({ fixture, mid }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    useConversationStore.setState({ searchTarget: { token: Date.now(), sessionId: fixture,
      anchorEventUuid: mid, query: "合成过程" } });
  }, { fixture, mid: `${inferred.id}:before` });
  await processText(row, "inferred").waitFor({ state: "visible" });
  await row.locator(`[data-search-anchor="${inferred.id}:before"]`).waitFor({ state: "visible" });
  await toggle(row, true).waitFor({ state: "visible" });
  await snapshot("inferred-original-mid-navigation");
  report.checks.push("Synthetic search navigation target using the original text mid reopens its inferred process section; navigation UI only");

  const native = message("native", { assistantPhase: { mode: "native", validation: "valid" } });
  native.blocks[0].phase = "commentary";
  native.blocks[2].phase = "final_answer";
  native.blocks.splice(2, 0, text(`${native.id}:unknown`, "native 未知阶段正文保持可见。"));
  row = await stage(native);
  await source(row, "native");
  await toggle(row, false).waitFor({ state: "visible" });
  await processText(row, "native").waitFor({ state: "hidden" });
  await answerText(row, "native").waitFor({ state: "visible" });
  await row.getByText("native 未知阶段正文保持可见。", { exact: true }).waitFor({ state: "visible" });
  await snapshot("native-valid");
  report.checks.push("Native valid completion retains its phase-based behavior: commentary folds, unknown and final remain visible");

  const live = message("transition", { status: "streaming", runOutcome: undefined, runFinishedAt: undefined });
  row = await stage(live);
  await source(row, "none");
  await processText(row, "transition").waitFor({ state: "visible" });
  await row.getByRole("button", { name: /计算/ }).waitFor({ state: "visible" });
  assert.equal(await row.getByRole("button", { name: /执行过程/ }).count(), 0);
  await snapshot("streaming-expanded");
  row = await stage({ ...live, status: "complete", runOutcome: "completed", runFinishedAt: finishedAt });
  await source(row, "inferred");
  await toggle(row, false).waitFor({ state: "visible" });
  await processText(row, "transition").waitFor({ state: "hidden" });
  await answerText(row, "transition").waitFor({ state: "visible" });
  await snapshot("trusted-completion-collapsed");
  report.checks.push("Same mounted message stays expanded while streaming, then folds after synthetic trustworthy completed outcome");

  for (const [name, overrides] of [
    ["cancelled", { status: "interrupted", runOutcome: "cancelled" }],
    ["failed", { status: "error", runOutcome: "failed", error: "合成错误保持可见。" }],
    ["old-history", { runOutcome: undefined, runStartedAt: undefined, runFinishedAt: undefined }],
    ["legacy-conflict", { assistantPhase: { mode: "legacy", validation: "conflict" } }],
  ]) {
    row = await stage(message(name, overrides));
    await source(row, "none");
    await processText(row, name).waitFor({ state: "visible" });
    await answerText(row, name).waitFor({ state: "visible" });
    await row.getByRole("button", { name: /计算/ }).waitFor({ state: "visible" });
    assert.equal(await row.getByRole("button", { name: /执行过程/ }).count(), 0);
    if (name === "failed") await row.getByText("合成错误保持可见。", { exact: true }).waitFor({ state: "visible" });
    await snapshot(name);
    report.checks.push(`${name}: no inferred collapse, process and answer remain visible`);
  }

  const direct = message("direct");
  direct.blocks = [direct.blocks[2]];
  row = await stage(direct);
  await source(row, "inferred");
  await answerText(row, "direct").waitFor({ state: "visible" });
  assert.equal(await row.getByRole("button", { name: /执行过程/ }).count(), 0);
  await snapshot("direct-answer-no-empty-toggle");
  report.checks.push("Direct final-only answer has no empty execution toggle");
  assert.deepEqual(blockedWrites, [], "The probe unexpectedly attempted a mutating HTTP request");
  report.passed = true;
} catch (error) {
  report.error = String(error);
  report.stack = error.stack;
  if (page) await snapshot("failure").catch(() => {});
} finally {
  if (page && staged) {
    try {
      report.restored = await page.evaluate(async ({ backupKey, fixture }) => {
        const { useConversationStore } = await import("/src/stores/conversation.ts");
        const { useDraftStore } = await import("/src/stores/drafts.ts");
        const backup = window[backupKey];
        if (!backup) throw new Error("Missing original in-memory view backup");
        if (backup.clipboardCaptureInstalled) {
          if (backup.clipboardWriteDescriptor) Object.defineProperty(backup.clipboard, "writeText", backup.clipboardWriteDescriptor);
          else delete backup.clipboard.writeText;
        }
        window.getSelection()?.removeAllRanges();
        useConversationStore.setState(backup.fields);
        await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
        // Composer creates a draft validation entry on session selection. Restore
        // just this fixture entry; never discard or overwrite existing user drafts.
        useDraftStore.setState((state) => {
          const workspaces = { ...state.workspaces };
          if (backup.fixtureDraftExisted) workspaces[fixture] = backup.fixtureDraft;
          else delete workspaces[fixture];
          return { workspaces };
        });
        const viewport = document.querySelector(".chat-message-viewport");
        if (viewport) viewport.scrollTop = backup.scrollTop;
        delete window[backupKey];
        return true;
      }, { backupKey, fixture });
    } catch (error) { report.restorationError = String(error); report.passed = false; }
  }
  if (page && routeInstalled) await page.unroute("**/*", guardWrites).catch(() => {});
  report.blockedMutatingRequests = blockedWrites;
  report.suppressedPresenceHeartbeats = suppressedPresenceHeartbeats;
  report.suppressedFixtureRuntimeReads = suppressedFixtureRuntimeReads;
  report.suppressedVoicePrewarms = suppressedVoicePrewarms;
  report.allowedTauriIpcRequests = [...allowedTauriIpcRequests.values()];
  if (blockedWrites.length > 0) report.passed = false;
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2), { flag: "wx" });
  if (browser) await browser.close(); // Disconnect the CDP client; do not stop Tauri.
}
console.log(JSON.stringify(report));
if (!report.passed) process.exitCode = 1;
