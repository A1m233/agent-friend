// Opt-in real WebView inspection; never creates a browser or sends a chat turn.
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
import assert from "node:assert/strict";

const require = createRequire(resolve("frontend/package.json"));
const { chromium } = require("playwright");
const [outputDirectory, action = "inspect"] = process.argv.slice(2);
if (!outputDirectory) throw new Error("A fixture/output directory is required");
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222");
try {
  const pages = browser.contexts().flatMap((context) => context.pages());
  const inventory = await Promise.all(pages.map(async (page) => ({ title: await page.title(), url: page.url() })));
  console.log(JSON.stringify(inventory));
  const page = pages.find((item) => item.url().includes("chat.html"));
  if (!page) throw new Error("Expected the desktop chat WebView");
  const job = () => page.evaluate(async () => (await import("/src/services/diagnostics.ts")).getDiagnosticJob());
  const waitJob = async (predicate, timeout = 120000) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const snapshot = await job();
      if (snapshot && predicate(snapshot)) return snapshot;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("Timed out waiting for the diagnostic job");
  };
  const openSection = async (name) => {
    const toggle = page.getByRole("button", { name, exact: true });
    if (await toggle.getAttribute("aria-expanded") === "false") await toggle.click();
  };
  if (action === "focus") {
    const heading = page.getByRole("heading", {name:"帮助与反馈", exact:true});
    if (!await heading.isVisible()) {
      const feedback = page.getByRole("button", {name:"帮助与反馈", exact:true});
      if (!await feedback.isVisible()) await page.getByRole("button", {name:"设置", exact:true}).click();
      await feedback.click();
    }
    await page.getByRole("checkbox", {name:/^选择会话 /}).first().waitFor();
    const requests = new Set(), responses = [];
    const isSessionList = (url) => new URL(url).pathname === "/v1/sessions";
    const onRequest = (request) => { if (isSessionList(request.url())) requests.add(request); };
    const onResponse = (response) => { if (requests.has(response.request())) responses.push(response.status()); };
    page.on("request", onRequest);
    page.on("response", onResponse);
    let frames;
    try {
      frames = await page.evaluate(async () => {
        const textarea = document.querySelector('[aria-label="问题说明"]');
        const card = textarea.closest("section");
        const list = card.querySelector('[data-slot="scroll-area"]');
        const rows = [...list.querySelectorAll('[role="checkbox"]')];
        const button = [...card.querySelectorAll("button")].find((item) => item.textContent === "生成诊断包");
        const views = [...document.querySelectorAll('[data-slot="scroll-area-viewport"]')];
        const box = (element) => { const r=element.getBoundingClientRect(); return [r.x,r.y,r.width,r.height]; };
        const samples = [];
        const sample = () => samples.push({
          geometry: { viewport:[innerWidth,innerHeight,devicePixelRatio], list:box(list), textarea:box(textarea), button:box(button), scroll:views.map(v=>[v.scrollTop,v.scrollLeft]) },
          rowCount:list.querySelectorAll('[role="checkbox"]').length,
          originalRowsConnected:rows.every(row=>row.isConnected),
          checked:rows.map(row=>row.getAttribute("aria-checked")),
          loadingVisible:list.textContent.includes("正在加载会话"),
        });
        const observer = new MutationObserver(sample);
        observer.observe(list,{childList:true,subtree:true});
        const timer = setInterval(sample,16);
        try {
          sample();
          for(let i=0;i<3;i++) {
            window.dispatchEvent(new Event("focus"));
            await new Promise(resolve=>setTimeout(resolve,600));
          }
          sample();
          return samples;
        } finally { observer.disconnect(); clearInterval(timer); }
      });
    } finally {
      page.off("request", onRequest);
      page.off("response", onResponse);
    }
    const initial = frames[0];
    assert.ok(initial.rowCount > 0);
    assert.equal(requests.size,3,"Each focus must still refresh real session metadata");
    assert.equal(responses.length,3);
    assert.ok(responses.every(status=>status===200));
    assert.ok(frames.every(frame=>frame.originalRowsConnected && !frame.loadingVisible && frame.rowCount===initial.rowCount));
    assert.ok(frames.every(frame=>JSON.stringify(frame.geometry)===JSON.stringify(initial.geometry)),"Focus refresh must not move the form or scroll it");
    assert.ok(frames.every(frame=>JSON.stringify(frame.checked)===JSON.stringify(initial.checked)));
    const report = {action,source:"actual Tauri WebView CDP; synthetic focus events, real metadata responses",focusCount:3,requests:requests.size,responses,frames};
    await writeFile(resolve(outputDirectory,"desktop-focus.json"),JSON.stringify(report,null,2));
    console.log(JSON.stringify({action,passed:true,focusCount:3,samples:frames.length,verticalShiftPx:0,rowsRetained:true,selectionRetained:true}));
  } else if (action === "layout") {
    assert.ok(!["running", "cancelling", "publishing"].includes((await job())?.status), "Do not interrupt an active export");
    const main = page.getByRole("main");
    const advanced = page.getByRole("button", { name: "高级选项", exact: true });
    const selected = page.getByLabel("选择会话 诊断验收 · 合成的 20 轮对话", { exact: true });
    if (!await selected.isVisible()) {
      const feedback = page.getByRole("button", {name:"帮助与反馈", exact:true});
      if (!await feedback.isVisible()) await page.getByRole("button", {name:"设置", exact:true}).click();
      await feedback.click();
    }
    await selected.waitFor();
    for (const box of await main.getByRole("checkbox", {name:/^选择会话 /}).all()) await box.setChecked(false);
    await selected.setChecked(true);
    await page.getByLabel("采集全部轮次", { exact: true }).setChecked(false);
    await page.getByLabel("采集最近轮数", { exact: true }).fill("5");
    await openSection("高级选项");
    const options = ["收集全部已保留日志", "校验安装文件完整性", "包含相关原始附件", "包含原始崩溃转储"];
    for (const name of options) await page.getByLabel(name, {exact:true}).setChecked(false);
    await page.getByLabel("校验安装文件完整性", {exact:true}).setChecked(true);
    await advanced.focus();
    await advanced.press("Enter");
    assert.equal(await advanced.getAttribute("aria-expanded"), "false");
    assert.ok(await main.getByText("高级选项已开启：安装文件校验。", {exact:true}).isVisible());
    await advanced.press("Enter");
    assert.equal(await page.getByLabel("校验安装文件完整性", {exact:true}).isChecked(), true);
    await page.getByLabel("校验安装文件完整性", {exact:true}).setChecked(false);
    await page.screenshot({path: resolve(outputDirectory, "desktop-layout-advanced.png")});
    await advanced.click();
    assert.equal(await page.getByLabel("问题说明", {exact:true}).isVisible(), true);
    assert.equal(await page.getByLabel("收集全部已保留日志", {exact:true}).isVisible(), false);
    assert.equal(await main.getByText("桌宠显示异常记录", {exact:true}).isVisible(), false);
    const heading = page.getByRole("heading", {name:"帮助与反馈", exact:true});
    await main.locator('[data-slot="scroll-area-viewport"]').filter({has:heading}).last().evaluate((element) => { element.scrollTop = 0; });
    const headingBounds = await heading.boundingBox();
    assert.ok(headingBounds && headingBounds.y >= 0, "The settings page must actually be at the top");
    const button = page.getByRole("button", {name:"生成诊断包", exact:true});
    const bounds = await button.boundingBox();
    const viewport = await page.evaluate(() => ({width: innerWidth, height: innerHeight}));
    assert.ok(bounds && bounds.y >= 0 && bounds.y + bounds.height <= viewport.height, "Export button must fit in the initial desktop viewport");
    const overflow = await main.evaluate((element) => element.scrollWidth > element.clientWidth);
    assert.equal(overflow, false);
    await page.screenshot({path: resolve(outputDirectory, "desktop-layout-default.png")});
    const previousJobId = (await job())?.id;
    await button.click();
    const terminal = await waitJob((value) => value.id !== previousJobId && ["complete", "partial", "failed", "cancelled"].includes(value.status));
    assert.equal(terminal.status, "complete");
    assert.equal(terminal.selection.sessionCount, 1);
    assert.equal(terminal.selection.sessionTurnLimit, 5);
    assert.equal(terminal.selection.logs, "recent");
    assert.equal(terminal.selection.verifyInstallFiles, false);
    await main.getByText(terminal.result.path, {exact:true}).waitFor();
    const report = {synthetic:true, action, viewport, buttonBounds:bounds, headingBounds, descriptionVisible:true, advancedCollapsed:true, horizontalOverflow:overflow, keyboardToggle:true, advancedChoiceRetained:true, terminal};
    await writeFile(resolve(outputDirectory, "desktop-layout.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } else
  if (["recent", "cancel", "all", "materials"].includes(action)) {
    if (["running", "cancelling", "publishing"].includes((await job())?.status)) await waitJob((value) => !["running", "cancelling", "publishing"].includes(value.status));
    const main = page.getByRole("main");
    const progress = main.locator('[role="status"][aria-live="polite"]');
    const selected = page.getByLabel("选择会话 诊断验收 · 合成的 20 轮对话", { exact: true });
    await selected.setChecked(true);
    if (action === "materials") {
      for (const box of await page.getByRole("checkbox", {name:/选择会话 诊断验收 · 大会话/}).all()) await box.setChecked(true);
    }
    await openSection("高级选项");
    assert.equal(await page.getByLabel("校验安装文件完整性", { exact: true }).isChecked(), false);
    assert.equal(await page.getByLabel("包含相关原始附件", { exact: true }).isChecked(), false);
    assert.equal(await page.getByLabel("包含原始崩溃转储", { exact: true }).isChecked(), false);
    await page.getByLabel("包含相关原始附件", { exact: true }).setChecked(action === "materials");
    await page.getByLabel("收集全部已保留日志", { exact: true }).setChecked(["all", "cancel"].includes(action));
    await page.getByLabel("采集全部轮次", { exact: true }).setChecked(["all", "materials"].includes(action));
    assert.equal(await page.getByLabel("采集最近轮数", { exact: true }).inputValue(), "10");
    await page.getByLabel("问题说明", { exact: true }).fill(`Synthetic desktop acceptance: ${action}`);
    const started = Date.now();
    await page.getByRole("button", { name: "生成诊断包", exact: true }).click();
    let extra = {};
    if (action === "cancel") {
      const running = await waitJob((value) => value.stage === "logs" && value.processedBytes > 1024 * 1024);
      const duplicate = await page.evaluate(async () => {
        try {
          await (await import("/src/services/diagnostics.ts")).startDiagnosticBundle({sessionIds:[]});
          return null;
        } catch (error) { return String(error); }
      });
      assert.ok(duplicate, "busy start must be rejected");
      await page.getByRole("button", { name: "通用", exact: true }).click();
      await page.getByRole("button", { name: "帮助与反馈", exact: true }).click();
      await page.getByRole("button", { name: "取消导出", exact: true }).waitFor();
      assert.equal((await job()).id, running.id);
      const restoredText = await progress.innerText();
      assert.match(restoredText, /全部已保留/);
      const cancelledAt = Date.now();
      await page.getByRole("button", { name: "取消导出", exact: true }).click();
      await waitJob((value) => value.status === "cancelled");
      extra = { duplicateRejected: duplicate, restoredText, cancellationMs: Date.now() - cancelledAt };
      await main.getByText("诊断导出已取消", {exact:true}).waitFor();
    }
    const terminal = await waitJob((value) => ["complete", "partial", "failed", "cancelled"].includes(value.status));
    console.log(JSON.stringify(terminal));
    await writeFile(resolve(outputDirectory, `terminal-${action}.json`), JSON.stringify(terminal, null, 2));
    assert.equal(terminal.status, action === "cancel" ? "cancelled" : "complete");
    if (action !== "cancel") {
      await main.getByText(terminal.result.path, { exact: true }).waitFor();
      assert.equal(await page.getByLabel("问题说明", { exact: true }).inputValue(), "");
    }
    await progress.scrollIntoViewIfNeeded();
    const status = await progress.innerText();
    await page.screenshot({path: resolve(outputDirectory, `desktop-${action}-webview.png`)});
    const report = {synthetic:true, action, wallMs:Date.now()-started, terminal, status, ...extra};
    await writeFile(resolve(outputDirectory, `desktop-${action}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } else
  if (action === "prepare") {
    await page.getByRole("button", { name: "反馈问题", exact: true }).click();
    await page.getByLabel("问题说明", { exact: true }).waitFor();
    await page.getByLabel("选择会话 诊断验收 · 合成的 20 轮对话", { exact: true }).setChecked(true);
    await openSection("高级选项");
    await page.getByRole("button", { name: "生成诊断包", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(outputDirectory, "desktop-options-webview.png") });
    console.log(await page.getByRole("button").evaluateAll((items) => items.map((item) => ({ text: item.textContent, label: item.getAttribute("aria-label") }))));
  } else
  if (action === "inspect") {
    console.log(JSON.stringify(await job()));
    console.log(await page.getByRole("button").evaluateAll((items) => items.map((item) => ({ text: item.textContent, label: item.getAttribute("aria-label"), title: item.getAttribute("title") }))));
    await writeFile(resolve(outputDirectory, "desktop-inventory.json"), JSON.stringify(inventory, null, 2));
  } else {
    throw new Error(`Unknown action: ${action}`);
  }
} finally {
  await browser.close();
}
