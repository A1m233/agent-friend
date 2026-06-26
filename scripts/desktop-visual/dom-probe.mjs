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
  if (!out.cdp) throw new Error("--cdp is required");
  return out;
}

function pickPage(pages, opts) {
  const titleNeedle = opts["title-contains"];
  const urlNeedle = opts["url-contains"];
  return (
    pages.find((page) => {
      const title = page.__desktopVisualTitle ?? "";
      const titleOk = !titleNeedle || title.includes(titleNeedle);
      const urlOk = !urlNeedle || page.url().includes(urlNeedle);
      return titleOk && urlOk;
    }) ??
    pages[0]
  );
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const browser = await chromium.connectOverCDP(opts.cdp, { timeout: 10_000 });
  try {
    const pages = browser.contexts().flatMap((context) => context.pages());
    if (pages.length === 0) throw new Error("No pages found on the CDP endpoint.");

    await Promise.all(
      pages.map(async (page) => {
        await page.waitForLoadState("domcontentloaded", { timeout: 2_000 }).catch(() => {});
        page.__desktopVisualTitle = await page.title().catch(() => "");
      }),
    );

    const page = pickPage(pages, opts);
    if (!page) throw new Error("No matching page found.");

    const title = await page.title().catch(() => "");
    const bodyText = await page.locator("body").innerText({ timeout: 3_000 }).catch(() => "");
    const html = await page.locator("html").evaluate((el) => el.outerHTML).catch(() => "");
    const summary = {
      title,
      url: page.url(),
      bodyText,
      htmlPreview: html.slice(0, 10_000),
      pageCount: pages.length,
    };

    if (opts.output) {
      await writeFile(resolve(opts.output), `${JSON.stringify(summary, null, 2)}\n`);
      console.log(`===> DOM summary: ${resolve(opts.output)}`);
    } else {
      console.log(JSON.stringify(summary, null, 2));
    }

    if (opts["html-output"]) {
      await writeFile(resolve(opts["html-output"]), html);
      console.log(`===> full HTML: ${resolve(opts["html-output"])}`);
    }

    if (opts.screenshot) {
      await page.screenshot({ path: resolve(opts.screenshot), fullPage: true });
      console.log(`===> CDP screenshot: ${resolve(opts.screenshot)}`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
