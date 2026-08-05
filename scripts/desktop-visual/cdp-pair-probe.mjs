#!/usr/bin/env node
import { createRequire } from "node:module";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const requireFromFrontend = createRequire(resolve(root, "frontend/package.json"));
const { chromium } = requireFromFrontend("playwright");

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    if (value === undefined || value.startsWith("--")) {
      throw new Error(`Missing value for ${key}`);
    }
    out[key.slice(2)] = value;
    i += 1;
  }
  for (const required of ["cdp", "screenshot", "ready-file", "trigger-at-ms"]) {
    if (!out[required]) throw new Error(`--${required} is required`);
  }
  return out;
}

function pickPage(pages, opts) {
  const titleNeedle = opts["title-contains"];
  const urlNeedle = opts["url-contains"];
  if (!titleNeedle && !urlNeedle) return pages[0];
  return pages.find((page) => {
    const title = page.__pairCaptureTitle ?? "";
    const titleOk = !titleNeedle || title.includes(titleNeedle);
    const urlOk = !urlNeedle || page.url().includes(urlNeedle);
    return titleOk && urlOk;
  });
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const triggerAtMs = Number(opts["trigger-at-ms"]);
  if (!Number.isFinite(triggerAtMs)) throw new Error("--trigger-at-ms must be numeric");

  const browser = await chromium.connectOverCDP(opts.cdp, {
    timeout: 10_000,
    noDefaults: true,
  });
  try {
    const pages = browser.contexts().flatMap((context) => context.pages());
    if (pages.length === 0) throw new Error("No pages found on the CDP endpoint.");
    await Promise.all(
      pages.map(async (page) => {
        page.__pairCaptureTitle = await page.title().catch(() => "");
      }),
    );
    const page = pickPage(pages, opts);
    if (!page) {
      throw new Error(
        `No page matched title=${opts["title-contains"] ?? "<any>"} url=${opts["url-contains"] ?? "<any>"}.`,
      );
    }

    await writeFile(
      resolve(opts["ready-file"]),
      `${JSON.stringify({
        status: "ready",
        title: await page.title().catch(() => ""),
        url: page.url(),
        pageCount: pages.length,
      })}\n`,
    );

    const delayMs = Math.max(0, triggerAtMs - Date.now());
    if (delayMs > 0) await new Promise((resolveDelay) => setTimeout(resolveDelay, delayMs));
    const captureStartedUnixMs = Date.now();
    await page.screenshot({ path: resolve(opts.screenshot), fullPage: false });
    const captureCompletedUnixMs = Date.now();
    const viewport = page.viewportSize();
    console.log(
      JSON.stringify({
        status: "captured",
        title: await page.title().catch(() => ""),
        url: page.url(),
        pageCount: pages.length,
        viewport,
        screenshot: resolve(opts.screenshot),
        captureStartedUnixMs,
        captureCompletedUnixMs,
      }),
    );
  } finally {
    // connectOverCDP marks the connection as remote, so close() disconnects this
    // client instead of terminating the existing WebView2 browser process.
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
