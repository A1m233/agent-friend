// Attach to an already identity-checked Tauri WebView. Never starts ASR or sends messages.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { chromium } = require("playwright");
assert.ok(process.argv[2], "usage: run OUTPUT_DIRECTORY [CDP_URL]; output must not exist");
const out = resolve(process.argv[2]);
await mkdir(out, { recursive: false });
const browser = await chromium.connectOverCDP(process.argv[3] ?? "http://127.0.0.1:9222");
const report = { syntheticTranscripts: true, realAsrCalls: 0, checks: [], passed: false };
let page;
try {
  page = browser.contexts().flatMap((context) => context.pages())
    .find((candidate) => new URL(candidate.url()).pathname === "/chat.html");
  assert.ok(page, "open the real chat window first");
  page.setDefaultTimeout(8000);
  assert.ok(await page.evaluate(() => !!window.__TAURI_INTERNALS__), "must be a Tauri WebView");
  report.url = page.url();
  await page.evaluate(async () => {
    const { useVoiceInputStore: voice } = await import("/src/stores/voiceInput.ts");
    const { useDraftStore: drafts } = await import("/src/stores/drafts.ts");
    if (["recording", "connecting", "stopping", "waiting_permission_flow"].includes(voice.getState().phase)) {
      throw new Error("refusing to take over an existing recording");
    }
    const savedVoice = voice.getState();
    const savedDrafts = drafts.getState().workspaces;
    let run = 0;
    window.__voiceInputProbe = {
      voice, seq: 0,
      restore() { voice.getState().draftSession?.close(); voice.setState(savedVoice); drafts.setState({ workspaces: savedDrafts }); },
    };
    voice.setState({
      prewarm: async () => {},
      start: async (targetWorkspaceKey) => voice.setState({
        phase: "recording", traceId: `synthetic-voice-${++run}`, targetWorkspaceKey,
        latestTranscript: null, error: null, errorCode: null,
      }),
      stop: async () => voice.setState({ phase: "idle", traceId: null, targetWorkspaceKey: null }),
    });
  });
  const input = page.locator("t-chat-sender textarea");
  await input.waitFor({ state: "visible" });
  const begin = async (text = "", caret) => {
    const stop = page.getByRole("button", { name: "停止语音输入", exact: true });
    if (await stop.count()) await stop.click();
    await input.fill(text);
    await input.focus();
    if (caret !== undefined) {
      await input.press("Home");
      for (let i = 0; i < caret; i++) await input.press("ArrowRight");
    }
    await page.getByRole("button", { name: "开始语音输入", exact: true }).click();
  };
  const emit = async (text, extra = {}) => page.evaluate(({ text, extra }) => {
    const probe = window.__voiceInputProbe;
    const state = probe.voice.getState();
    probe.voice.setState({ latestTranscript: {
      text, seq: ++probe.seq, kind: "partial", traceId: state.traceId,
      targetWorkspaceKey: state.targetWorkspaceKey, ...extra,
    } });
  }, { text, extra });
  const check = async (name, text, caret) => {
    await page.waitForFunction(({ text, caret }) => {
      const scan = (root) => {
        const textarea = root.querySelector("textarea");
        if (textarea) return textarea;
        for (const child of root.querySelectorAll("*")) {
          if (child.shadowRoot) { const result = scan(child.shadowRoot); if (result) return result; }
        }
      };
      const textarea = scan(document.querySelector("t-chat-sender").shadowRoot);
      return textarea?.value === text && (caret === undefined || textarea.selectionStart === caret);
    }, { text, caret }, { timeout: 5000 });
    report.checks.push({ name, text, caret });
  };
  await begin();
  await emit("今天天气很好");
  await check("initial partial", "今天天气很好");
  await input.fill("今天天气不错");
  await emit("今天天气很好我们出去走走");
  await check("edit then continue", "今天天气不错我们出去走走");
  await input.fill("");
  await emit("今天天气很好我们出去走走明天再来");
  await check("clear then continue", "明天再来");
  await begin("前后", 1);
  await emit("中");
  await check("insert at caret", "前中后", 2);
  await emit("中间");
  await check("revise only live range", "前中间后", 3);
  await input.press("Home");
  await emit("中间新增");
  await check("move caret then continue", "新增前中间后", 2);
  await begin();
  await emit("今天");
  await check("before IME", "今天");
  await input.dispatchEvent("compositionstart", { bubbles: true, composed: true });
  await input.evaluate((el) => {
    el.value = "今天hao"; el.setSelectionRange(5, 5);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, isComposing: true }));
  });
  await emit("今天下雨");
  await check("synthetic IME pending", "今天hao");
  await input.evaluate((el) => {
    el.value = "今天好"; el.setSelectionRange(3, 3);
    el.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, composed: true }));
  });
  await check("synthetic IME commit", "今天好下雨", 5);
  await begin();
  await emit("一百个苹果", { spans: [{ text: "一百个苹果", startMs: 0, endMs: 1000 }] });
  await check("timed partial", "一百个苹果");
  await input.fill("两百个苹果");
  await emit("100个苹果然后回家", { spans: [
    { text: "100个苹果", startMs: 0, endMs: 1000 }, { text: "然后回家", startMs: 1100, endMs: 2000 },
  ] });
  await check("number revision across audio boundary", "两百个苹果然后回家");
  await input.screenshot({ path: resolve(out, "composer.png") });
  report.passed = true;
} catch (error) {
  report.error = String(error);
  if (page) report.input = await page.locator("t-chat-sender textarea").inputValue({ timeout: 1000 }).catch(() => null);
} finally {
  if (page) await page.evaluate(() => {
    window.__voiceInputProbe?.restore(); delete window.__voiceInputProbe;
  }).catch(() => {});
  await writeFile(resolve(out, "report.json"), JSON.stringify(report, null, 2), { flag: "wx" });
  await browser.close();
}
console.log(JSON.stringify(report));
if (!report.passed) process.exitCode = 1;
