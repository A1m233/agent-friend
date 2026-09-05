import { createRequire } from "node:module";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { chromium } = require("playwright");
const fixture = JSON.parse(await readFile(process.argv[2], "utf8"));
assert.equal(fixture.synthetic, true);
const out = resolve(process.argv[3]);
await mkdir(out, { recursive: false });
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { timeout: 10000 });
const report = { synthetic: true, llmCalls: 0, checks: [], passed: false };
let page;
try {
  page = browser.contexts().flatMap((context) => context.pages()).find((candidate) => new URL(candidate.url()).pathname === "/chat.html");
  assert.ok(page, "chat WebView must exist");
  report.url = page.url();
  report.viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  await page.evaluate(async (sessionId) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    await useConversationStore.getState().openSession(sessionId);
  }, fixture.session_id);
  // Each probe starts from persisted defaults, not a prior probe's manual view state.
  await page.reload();
  const native = page.locator(`[data-message-id="${fixture.ids.native_a}"]`);
  const legacy = page.locator(`[data-message-id="${fixture.ids.legacy_text}"]`);
  const interrupted = page.locator(`[data-message-id="${fixture.ids.interrupted_text}"]`);
  await native.waitFor({ state: "visible", timeout: 15000 });
  const toggle = native.getByRole("button", { name: "展开执行过程", exact: true });
  await toggle.waitFor({ state: "visible" });
  await native.scrollIntoViewIfNeeded();
  await page.locator(".chat-message-viewport").evaluate((viewport, messageId) => {
    const row = viewport.querySelector(`[data-message-id="${messageId}"]`);
    if (row) viewport.scrollTop -= Math.max(0, viewport.getBoundingClientRect().top + 24 - row.getBoundingClientRect().top);
  }, fixture.ids.native_a);
  assert.ok((await native.innerText()).includes("用时 1分 52秒"));
  assert.ok(!(await native.innerText()).includes("合成过程A"));
  await native.getByText("未知阶段：这段文字始终可见。", { exact: true }).waitFor({ state: "visible" });
  await native.getByText("合成最终回答：本轮检查完成。", { exact: true }).waitFor({ state: "visible" });
  await page.mouse.move(5, 5);
  const readHeaderStyle = () => toggle.evaluate((element) => {
    const style = getComputedStyle(element);
    return { color: style.color, background: style.backgroundColor, shadow: style.boxShadow,
      fontSize: style.fontSize, lineHeight: style.lineHeight, paddingLeft: style.paddingLeft, paddingRight: style.paddingRight };
  });
  report.headerStyle = { normal: await readHeaderStyle() };
  const bodyText = native.getByText("未知阶段：这段文字始终可见。", { exact: true });
  report.headerStyle.body = await bodyText.evaluate((element) => {
    const style = getComputedStyle(element);
    return { fontSize: style.fontSize, lineHeight: style.lineHeight };
  });
  report.headerStyle.durationBounds = await native.locator("[data-run-duration]").boundingBox();
  report.headerStyle.bodyBounds = await bodyText.boundingBox();
  report.headerStyle.separatorBounds = await native.locator("[data-run-separator]").boundingBox();
  assert.ok(report.headerStyle.separatorBounds.height > 0 && report.headerStyle.separatorBounds.height < 1, "run separator must be a visible subpixel hairline");
  assert.equal(report.headerStyle.normal.fontSize, report.headerStyle.body.fontSize);
  assert.equal(report.headerStyle.normal.lineHeight, report.headerStyle.body.lineHeight);
  // Border widths are device-pixel rounded by WebView2; align within one CSS pixel.
  assert.ok(Math.abs(report.headerStyle.durationBounds.x - report.headerStyle.bodyBounds.x) < 1);
  assert.ok(report.headerStyle.separatorBounds.y >= report.headerStyle.durationBounds.y + report.headerStyle.durationBounds.height);
  assert.ok(report.headerStyle.separatorBounds.y + report.headerStyle.separatorBounds.height <= report.headerStyle.bodyBounds.y);
  await toggle.hover();
  await page.waitForTimeout(250);
  report.headerStyle.hover = await readHeaderStyle();
  assert.deepEqual(report.headerStyle.hover, report.headerStyle.normal, "hover must not recolor or resize the run header");
  await page.screenshot({ path: resolve(out, "header-hover.png") });
  await page.mouse.move(5, 5);
  report.checks.push("run header: body-size typography, text alignment within one CSS pixel, separator below, hover unchanged");
  report.checks.push("native collapsed; unknown/final visible; persisted duration 112s");
  await page.screenshot({ path: resolve(out, "collapsed.png") });
  await toggle.click();
  await native.getByText("合成过程A：检查现有业务。", { exact: true }).waitFor({ state: "visible" });
  await native.getByText("合成过程B：检查已完成。", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await native.getByRole("button", { name: "收起执行过程", exact: true }).count(), 1);
  report.checks.push("one real click expands both separated process sections");
  await page.screenshot({ path: resolve(out, "expanded.png") });
  await native.getByRole("button", { name: "收起执行过程", exact: true }).click();
  await legacy.scrollIntoViewIfNeeded();
  assert.ok((await legacy.innerText()).includes("用时 8秒"));
  assert.equal(await legacy.getByRole("button", { name: /执行过程/ }).count(), 0);
  await interrupted.scrollIntoViewIfNeeded();
  assert.equal(await interrupted.locator("[data-run-duration]").innerText(), "已停止，用时未知");
  assert.equal(await interrupted.locator("[data-run-outcome]").count(), 0);
  await interrupted.getByText("合成中断：不应继续计时或自动收起。", { exact: true }).waitFor({ state: "visible" });
  report.checks.push("legacy has time and no toggle; interrupted remains visible with unknown duration");
  await page.reload();
  await native.waitFor({ state: "visible", timeout: 15000 });
  await native.getByRole("button", { name: "展开执行过程", exact: true }).waitFor({ state: "visible" });
  assert.ok((await native.innerText()).includes("用时 1分 52秒"));
  report.checks.push("real WebView reload restores native disclosure under the persisted legacy current model");
  await page.keyboard.press("Control+f");
  const searchDialog = page.getByRole("dialog", { name: "搜索全部对话", exact: true });
  await searchDialog.waitFor({ state: "visible" });
  await searchDialog.getByLabel("搜索全部对话", { exact: true }).fill("合成过程A");
  await searchDialog.getByText("合成过程A", { exact: true }).waitFor({ state: "visible" });
  await page.keyboard.press("Enter");
  await searchDialog.waitFor({ state: "hidden" });
  const commentary = native.getByText("合成过程A：检查现有业务。", { exact: true });
  await commentary.waitFor({ state: "visible" });
  await page.locator(`[data-search-anchor="${fixture.ids.native_a}"]`).waitFor({ state: "visible" });
  report.checks.push("real global search query and Enter navigation expands hidden commentary");
  await page.screenshot({ path: resolve(out, "search-reveal.png") });
  // DOM Range provides a deterministic selection; the quotation action itself
  // is a real pointer click in the desktop WebView, not a store insertion.
  await commentary.evaluate((element) => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    window.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  });
  const quote = page.getByRole("button", { name: "引用", exact: true });
  await quote.waitFor({ state: "visible" });
  await quote.click();
  report.quote = await page.evaluate(async (sessionId) => {
    const { useDraftStore } = await import("/src/stores/drafts.ts");
    const state = useDraftStore.getState();
    const reference = state.workspaces[sessionId]?.references.at(-1);
    return reference ? { referenceId: reference.referenceId, source: reference.source, text: reference.quotedText } : null;
  }, fixture.session_id);
  assert.equal(report.quote?.source.textBlockId, fixture.ids.native_a);
  assert.ok(report.quote?.text.includes("合成过程A"));
  report.checks.push("expanded commentary can be selected and quoted via real button click");
  const clockRun = `${fixture.session_id}:ui-clock`;
  await page.evaluate(async ({ sessionId, runId }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const state = useConversationStore.getState();
    if (state.currentSessionId !== sessionId) throw new Error("fixture no longer selected");
    const createdAt = new Date().toISOString();
    const messages = [...state.messages, { id: runId, runId, role: "assistant", status: "streaming", createdAt,
      assistantPhase: { mode: "legacy", validation: "pending" }, blocks: [] }];
    const pending = { run_id: runId, session_id: sessionId, status: "queued", outcome: null,
      reason_code: "", channel: "text", origin: "synthetic-desktop-probe", control_scope: "desktop_text",
      created_at: createdAt, started_at: null, finished_at: null, version: 0 };
    useConversationStore.setState({ messages, runSnapshots: { ...state.runSnapshots, [runId]: pending },
      sessionViews: { ...state.sessionViews, [sessionId]: { ...state.sessionViews[sessionId], messages } } });
  }, { sessionId: fixture.session_id, runId: clockRun });
  const clockSelector = `[data-message-id="${clockRun}"]`;
  const clockRow = page.locator(clockSelector);
  await clockRow.waitFor({ state: "visible" });
  await page.locator(".chat-message-viewport").evaluate((viewport) => {
    viewport.scrollTop = viewport.scrollHeight;
    viewport.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  const assertPendingClock = async () => {
    await clockRow.locator("[data-message-activity-tail]").waitFor({ state: "visible" });
    assert.equal(await clockRow.locator("[data-run-duration], [data-run-separator], [data-run-status-line]").count(), 0);
    assert.equal(await clockRow.getByRole("button", { name: /执行过程/ }).count(), 0);
    assert.ok(!(await clockRow.innerText()).includes("用时未知"));
  };
  await assertPendingClock();
  // Search/quote navigation preceded this UI-only insertion; explicitly resume
  // following as a real send would, instead of equating a DOM scroll with intent.
  const followButton = page.getByRole("button", { name: "滚到底部", exact: true });
  if (await followButton.isVisible()) await followButton.click();
  await page.evaluate(async (runId) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const state = useConversationStore.getState();
    state.applyRunSnapshot({ ...state.runSnapshots[runId], version: 1 });
  }, clockRun);
  await page.waitForTimeout(2200);
  await assertPendingClock();
  await page.screenshot({ path: resolve(out, "before-start.png") });
  report.startTransition = await page.evaluate(async ({ runId, selector }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const state = useConversationStore.getState();
    const started_at = new Date().toISOString();
    state.applyRunSnapshot({ ...state.runSnapshots[runId], status: "running", started_at, version: 2 });
    const samples = [];
    const until = performance.now() + 300;
    await new Promise((done) => {
      const sample = () => {
        const row = document.querySelector(selector);
        samples.push({ label: row?.querySelector("[data-run-duration]")?.textContent ?? null,
          separator: Boolean(row?.querySelector("[data-run-separator]")) });
        if (performance.now() < until) requestAnimationFrame(sample); else done();
      };
      requestAnimationFrame(sample);
    });
    return { queuedMs: Date.parse(started_at) - Date.parse(state.runSnapshots[runId].created_at), samples };
  }, { runId: clockRun, selector: clockSelector });
  assert.ok(report.startTransition.queuedMs >= 2200);
  assert.ok(report.startTransition.samples.every(({ label, separator }) => label === null ? !separator : separator && /^已进行 [01]秒$/.test(label)));
  await clockRow.locator("[data-run-duration]").waitFor({ state: "visible" });
  assert.match(await clockRow.locator("[data-run-duration]").innerText(), /^已进行 [01]秒$/);
  await page.waitForFunction((selector) => {
    const tail = document.querySelector(selector)?.querySelector("[data-message-activity-tail]")?.getBoundingClientRect();
    const composer = document.querySelector('[data-placement="bottom"]')?.getBoundingClientRect();
    return tail && composer && tail.top >= 50 && tail.bottom <= composer.top;
  }, clockSelector, { timeout: 4000 });
  report.startTransition.tailBounds = await clockRow.locator("[data-message-activity-tail]").boundingBox();
  report.startTransition.composerBounds = await page.locator('[data-placement="bottom"]').boundingBox();
  await page.screenshot({ path: resolve(out, "just-started.png") });
  report.checks.push("UI-only send/admission: no time header/separator while queued, activity stays; actual start shows live time without unknown flash or queue seconds");
  const firstTime = Number((await clockRow.innerText()).match(/已进行 (\d+)秒/)[1]);
  assert.equal(await clockRow.getByRole("button", { name: /执行过程/ }).count(), 0);
  await page.waitForFunction(([selector, previous]) => Number(document.querySelector(selector)?.innerText.match(/已进行 (\d+)秒/)?.[1]) > previous, [clockSelector, firstTime], { timeout: 5000 });
  await page.evaluate(async (runId) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    useConversationStore.setState((state) => ({ stopRequestsByRun: { ...state.stopRequestsByRun, [runId]: "requesting" } }));
  }, clockRun);
  const stoppingTime = Number((await clockRow.innerText()).match(/已进行 (\d+)秒/)[1]);
  await page.waitForFunction(([selector, previous]) => Number(document.querySelector(selector)?.innerText.match(/已进行 (\d+)秒/)?.[1]) > previous, [clockSelector, stoppingTime], { timeout: 5000 });
  await page.screenshot({ path: resolve(out, "live-stopping.png") });
  await page.evaluate(async (runId) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const state = useConversationStore.getState();
    state.applyRunSnapshot({ ...state.runSnapshots[runId], status: "terminal", outcome: "cancelled", finished_at: new Date().toISOString(), version: 3 });
  }, clockRun);
  await clockRow.getByText(/^\d+秒 后被停止$/).waitFor({ state: "visible" });
  const fixed = await clockRow.locator("[data-run-duration]").innerText();
  assert.equal(await clockRow.locator("[data-run-outcome]").count(), 0);
  assert.equal(await clockRow.locator("[data-message-activity-tail]").count(), 0);
  await page.waitForTimeout(1500);
  assert.equal(await clockRow.locator("[data-run-duration]").innerText(), fixed);
  report.checks.push("UI-only legacy run: running label ticks through cancellation request, neutral stopped header freezes at terminal, no duplicate stopped tail");
  await page.screenshot({ path: resolve(out, "terminal-fixed.png") });
  // A separate native run exercises live layout without changing a run's frozen mode.
  const activityRun = `${fixture.session_id}:ui-activity`;
  const stageActivity = async (stage) => page.evaluate(async ({ sessionId, runId, stage }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const state = useConversationStore.getState();
    if (state.currentSessionId !== sessionId) throw new Error("fixture no longer selected");
    const previous = state.messages.find((message) => message.id === runId);
    const createdAt = previous?.createdAt ?? new Date(Date.now() - 4000).toISOString();
    const blocks = [];
    if (stage >= 1) blocks.push({ kind: "text", mid: `${runId}:text`, text: "合成进度：先检查相关文件。", phase: "commentary" });
    if (stage >= 2) blocks.push({ kind: "tool", toolCallId: `${runId}:tool`, name: "web_search",
      args: JSON.stringify({ query: "115 尾部活动合成检查" }), status: stage === 2 ? "running" : "done",
      ...(stage >= 3 ? { result: "合成工具结果，无网络调用" } : {}) });
    if (stage === 4) blocks.push({ kind: "text", mid: `${runId}:final`, text: "合成最终回答：尾部活动检查完成。", phase: "final_answer" });
    const message = { id: runId, runId, role: "assistant", status: "streaming", createdAt, blocks,
      assistantPhase: { mode: "native", validation: stage === 4 ? "valid" : "pending" },
      runStatus: { phase: "waiting", afterTool: stage >= 3, attempt: 1, maxAttempts: 2, activityVersion: stage + 1 } };
    const messages = [...state.messages.filter((item) => item.id !== runId), message];
    useConversationStore.setState({ messages, sessionViews: { ...state.sessionViews, [sessionId]: { ...state.sessionViews[sessionId], messages } } });
    if (stage === 0) state.applyRunSnapshot({ run_id: runId, session_id: sessionId, status: "running", outcome: null,
      reason_code: "", channel: "text", origin: "synthetic-desktop-probe", control_scope: "desktop_text",
      created_at: createdAt, started_at: createdAt, finished_at: null, version: 1 });
    if (stage === 4) state.applyRunSnapshot({ ...state.runSnapshots[runId], status: "terminal", outcome: "completed", finished_at: new Date().toISOString(), version: 2 });
  }, { sessionId: fixture.session_id, runId: activityRun, stage });
  await stageActivity(0);
  const activityRow = page.locator(`[data-message-id="${activityRun}"]`);
  const duration = activityRow.locator("[data-run-duration]");
  const tail = activityRow.locator("[data-message-activity-tail]");
  const scrollToTail = () => page.locator(".chat-message-viewport").evaluate((viewport) => {
    viewport.scrollTop = viewport.scrollHeight;
    viewport.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await activityRow.waitFor({ state: "visible" });
  await scrollToTail();
  await tail.getByText("正在等待模型响应…", { exact: true }).waitFor({ state: "visible" });
  assert.match(await duration.innerText(), /^已进行 \d+秒$/);
  assert.equal(await activityRow.getByRole("button", { name: /执行过程/ }).count(), 0);
  await stageActivity(1);
  const progressText = activityRow.getByText("合成进度：先检查相关文件。", { exact: true });
  await progressText.waitFor({ state: "visible" });
  await stageActivity(2);
  await activityRow.locator('[data-primary-activity="tool"]').waitFor({ state: "visible" });
  assert.equal(await tail.count(), 0, "running tool must not have a second model loading prompt");
  await stageActivity(3);
  await tail.getByText("正在整理工具结果…", { exact: true }).waitFor({ state: "visible" });
  await scrollToTail();
  const toolButton = activityRow.getByRole("button", { name: /搜索网页.*115 尾部活动合成检查/ });
  report.activityBounds = { duration: await duration.boundingBox(), text: await progressText.boundingBox(),
    tool: await toolButton.boundingBox(), tail: await tail.boundingBox() };
  const boxes = Object.values(report.activityBounds);
  assert.ok(boxes.every(Boolean));
  for (let index = 1; index < boxes.length; index++) {
    assert.ok(boxes[index].y >= boxes[index - 1].y + boxes[index - 1].height, "activity must follow duration, text and tool in visual order");
  }
  assert.equal(await activityRow.locator("[data-primary-activity]").count(), 1);
  await page.screenshot({ path: resolve(out, "activity-after-tool.png") });
  await activityRow.getByRole("button", { name: "收起执行过程", exact: true }).click();
  await progressText.waitFor({ state: "hidden" });
  await toolButton.waitFor({ state: "hidden" });
  await tail.getByText("正在整理工具结果…", { exact: true }).waitFor({ state: "visible" });
  assert.match(await activityRow.getByRole("button", { name: "展开执行过程", exact: true }).innerText(), /^已进行 \d+秒$/);
  await page.screenshot({ path: resolve(out, "activity-collapsed.png") });
  await stageActivity(4);
  await activityRow.getByText("合成最终回答：尾部活动检查完成。", { exact: true }).waitFor({ state: "visible" });
  assert.match(await duration.innerText(), /^用时 \d+秒$/);
  assert.equal(await tail.count(), 0);
  assert.equal(await activityRow.locator("[data-primary-activity]").count(), 0);
  report.checks.push("UI-only native run: independent timer, initial waiting, text/tool/tail visual order, no duplicate tool loading, tail survives manual collapse, terminal removes activity");
  await native.getByRole("button", { name: "收起执行过程", exact: true }).click();
  await page.evaluate(async ({ sessionId, referenceId }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const { useDraftStore } = await import("/src/stores/drafts.ts");
    const reference = useDraftStore.getState().workspaces[sessionId]?.references.find((item) => item.referenceId === referenceId);
    if (!reference) throw new Error("synthetic quote missing");
    useConversationStore.setState((state) => {
      const messages = [...state.messages, { id: `${sessionId}:ui-reference`, role: "user", createdAt: new Date().toISOString(),
        status: "complete", references: [reference], blocks: [{ kind: "text", mid: `${sessionId}:ui-reference`, text: "合成引用导航（不提交模型）" }] }];
      return { messages, sessionViews: { ...state.sessionViews, [sessionId]: { ...state.sessionViews[sessionId], messages } } };
    });
  }, { sessionId: fixture.session_id, referenceId: report.quote.referenceId });
  const referenceRow = page.locator(`[data-message-id="${fixture.session_id}:ui-reference"]`);
  await page.locator(".chat-message-viewport").evaluate((viewport) => { viewport.scrollTop = viewport.scrollHeight; });
  await referenceRow.getByRole("button", { name: "查看 1 个已选文本", exact: true }).hover();
  await page.getByRole("button", { name: /1\. Agent 消息/ }).click();
  await commentary.waitFor({ state: "visible" });
  await page.waitForFunction(() => CSS.highlights.has("agent-friend-quoted-text"), undefined, { timeout: 5000 });
  const deadline = Date.now() + 4000;
  do {
    report.referenceBounds = await commentary.boundingBox();
    if (report.referenceBounds && report.referenceBounds.y >= 50 && report.referenceBounds.y + report.referenceBounds.height < report.viewport.height - 170) break;
    await page.waitForTimeout(150);
  } while (Date.now() < deadline);
  assert.ok(report.referenceBounds && report.referenceBounds.y >= 50
    && report.referenceBounds.y + report.referenceBounds.height < report.viewport.height - 170, "reference must land visibly above the composer, not only expand offscreen");
  report.checks.push("synthetic quoted user message: real reference popover click reopens commentary and highlights original text");
  await page.screenshot({ path: resolve(out, "reference-reveal.png") });
  report.passed = true;
} catch (error) {
  report.error = String(error);
  report.stack = error.stack;
  if (page) {
    report.messageRows = await page.locator("[data-message-id]").evaluateAll((rows) => rows.map((row) => ({ id: row.getAttribute("data-message-id"), text: row.innerText })));
    await page.screenshot({ path: resolve(out, "failure.png") }).catch(() => {});
  }
} finally {
  if (page) await page.evaluate(async ({ sessionId, referenceId }) => {
    const { useConversationStore } = await import("/src/stores/conversation.ts");
    const { useDraftStore } = await import("/src/stores/drafts.ts");
    useConversationStore.setState((state) => {
      const runSnapshots = { ...state.runSnapshots };
      const stopRequestsByRun = { ...state.stopRequestsByRun };
      for (const suffix of ["ui-clock", "ui-activity"]) {
        delete runSnapshots[`${sessionId}:${suffix}`];
        delete stopRequestsByRun[`${sessionId}:${suffix}`];
      }
      return { runSnapshots, stopRequestsByRun };
    });
    if (referenceId) useDraftStore.getState().removeReference(sessionId, referenceId);
    if (useConversationStore.getState().currentSessionId === sessionId) await useConversationStore.getState().refreshCurrentSession(sessionId);
  }, { sessionId: fixture.session_id, referenceId: report.quote?.referenceId }).catch(() => {});
  await writeFile(resolve(out, "report.json"), JSON.stringify(report, null, 2), { flag: "wx" });
  await browser.close();
}
console.log(JSON.stringify(report));
if (!report.passed) process.exitCode = 1;
