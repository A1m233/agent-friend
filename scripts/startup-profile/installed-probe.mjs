import { createRequire } from "node:module";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { chromium } = require("playwright");
const [port, output, bridgePort, voicePort] = process.argv.slice(2);
const deadline = Date.now() + 180_000;
let browser;
let page;
async function waitNative(command, args, accepts, timeout = 120_000) {
  const until = Date.now() + timeout;
  let snapshot;
  while (Date.now() < until) {
    snapshot = await page.evaluate(({ command, args }) =>
      window.__TAURI_INTERNALS__.invoke(command, args), { command, args });
    if (accepts(snapshot)) return snapshot;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Native state did not become ready: ${command}`);
}
while (Date.now() < deadline && !browser) {
  try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 1500 }); }
  catch { await new Promise((resolve) => setTimeout(resolve, 500)); }
}
if (!browser) throw new Error("isolated WebView CDP unavailable");
try {
  while (Date.now() < deadline && !page) {
    page = browser.contexts().flatMap((context) => context.pages()).find((item) => /\/chat\.html(?:[?#]|$)/.test(item.url()));
    if (!page) await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!page) throw new Error("chat WebView missing");
  await page.waitForFunction(() => Boolean(window.__TAURI_INTERNALS__?.invoke));
  const endpoints = await page.evaluate(() => window.__AGENT_FRIEND_LOCAL_SERVICES__);
  if (endpoints?.profileId !== "installer-smoke"
      || endpoints.bridge !== `http://127.0.0.1:${bridgePort}`
      || endpoints.voice !== `http://127.0.0.1:${voicePort}`) {
    throw new Error("WebView bootstrap does not match isolated service endpoints");
  }
  const bridgeSnapshot = await waitNative("get_runtime_service_snapshot", { service: "bridge" },
    (snapshot) => snapshot.phase === "ready");
  const isolatedSessionCount = await page.evaluate(async (base) => {
    const response = await fetch(`${base}/v1/sessions`);
    if (!response.ok) throw new Error("isolated session query failed");
    const sessions = await response.json();
    if (!Array.isArray(sessions)) throw new Error("unexpected isolated session response");
    return sessions.length;
  }, endpoints.bridge);
  if (isolatedSessionCount !== 0) throw new Error("isolated first-run profile is not empty");
  await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("open_chat"));
  // The first-run permission flow may navigate to settings after open_chat.
  // Wait for its initial observation, then navigate using the real sidebar.
  await waitNative("get_permission_snapshot", {}, (snapshot) =>
    snapshot.snapshotRevision > 0 && !snapshot.refreshing
      && (snapshot.activeFlow || snapshot.items.every((item) => item.systemState === "granted"
        || item.handledForCurrentRevision || item.deferredForCurrentRevision)), 30_000);
  await page.getByRole("button", { name: "新对话", exact: true }).first().click();
  const editor = page.locator("textarea:visible").first();
  await editor.waitFor({ state: "visible", timeout: 120_000 });
  await page.waitForFunction(() => {
    function find(root) {
      for (const element of root.querySelectorAll("*")) {
        if (element.tagName === "TEXTAREA" && !element.disabled && !element.readOnly) return true;
        if (element.shadowRoot && find(element.shadowRoot)) return true;
      }
      return false;
    }
    return find(document);
  }, null, { timeout: 120_000 });
  // A real focus/input round trip; never submit a message or call a provider.
  await editor.fill("startup-evidence-local-only");
  const inputVerified = await editor.inputValue() === "startup-evidence-local-only";
  await editor.fill("");
  const requests = await page.evaluate(() => performance.getEntriesByType("resource")
    .map((entry) => entry.name)
    .filter((url) => /^https?:\/\/127\.0\.0\.1:/.test(url))
    .map((url) => new URL(url).origin));
  const routingVerified = requests.includes(endpoints.bridge)
    && requests.every((origin) => origin === endpoints.bridge || origin === endpoints.voice);
  if (!routingVerified) throw new Error("Observed frontend requests do not match isolated endpoints");
  const bundle = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("generate_diagnostic_bundle", {
    request: { description: "149 isolated startup evidence acceptance", sessionIds: [], locale: "zh-CN", timezone: "Asia/Shanghai", includeAttachmentOriginals: false, includeRenderEvidence: false },
  }));
  await writeFile(output, JSON.stringify({ surface: "desktop DOM via CDP", page: new URL(page.url()).pathname,
    editorEnabled: await editor.isEnabled(), inputVerified, routingVerified, endpoints, isolatedSessionCount, bridgeSnapshot,
    observedOrigins: [...new Set(requests)], bundle, harness: fileURLToPath(import.meta.url) }, null, 2), { flag: "wx" });
} catch (error) {
  if (page) {
    await writeFile(`${output}.failure.json`, JSON.stringify({
      error: String(error),
      editors: await page.locator("textarea").evaluateAll((elements) => elements.map((element) => ({
        connected: element.isConnected, disabled: element.disabled, readOnly: element.readOnly,
        width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height,
      }))),
    }, null, 2), { flag: "wx" });
    await page.screenshot({ path: `${output}.failure.png` });
  }
  throw error;
} finally { await browser.close(); }
