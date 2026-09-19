// Explicitly authorized Tauri verification. Never selects an existing user session.
import { createRequire } from "node:module";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { tmpdir } from "node:os";
const require = createRequire(resolve("frontend/package.json"));
const { chromium } = require("playwright");
const [stage, sessionId] = process.argv.slice(2);
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
try {
  let pages = browser.contexts().flatMap(c => c.pages());
  if (stage === "open") {
    const response = await fetch("http://127.0.0.1:18800/v1/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: sessionId, title: "137 recovery acceptance" }),
    });
    if (!response.ok) throw new Error(`create session: ${response.status}`);
    const { session_id } = await response.json();
    const renamed = await fetch(`http://127.0.0.1:18800/v1/sessions/${session_id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "137 recovery acceptance" }),
    });
    if (!renamed.ok) throw new Error(`rename acceptance session: ${renamed.status}`);
    await pages[0].evaluate(id => window.__TAURI_INTERNALS__.invoke("open_chat_session", { sessionId: id }), session_id);
    console.log(JSON.stringify({ sessionId: session_id }));
  } else {
    const page = pages.find(p => p.url().includes("chat.html"));
    if (!page) throw new Error("No Tauri chat WebView");
    const safe = await fetch(`http://127.0.0.1:18800/v1/sessions/${sessionId}`).then(r => r.json());
    const list = await fetch("http://127.0.0.1:18800/v1/sessions").then(r => r.json());
    if (stage !== "inspect") {
      if (list.find(s=>s.session_id===sessionId)?.title !== "137 recovery acceptance") throw new Error("Not the disposable acceptance session");
      const active = page.locator('button[data-active="true"]').filter({hasText:"137 recovery acceptance"});
      await active.waitFor({timeout:5000});
    }
    if (stage === "inspect") {
      console.log(JSON.stringify({ url: page.url(), title: await page.title(),
        inputs: await page.locator("textarea,input").evaluateAll(es => es.map(e=>({tag:e.tagName,placeholder:e.placeholder,type:e.type}))),
        buttons: await page.getByRole("button").evaluateAll(es=>es.map(e=>({text:e.innerText, label:e.getAttribute("aria-label")}))),
      }));
    } else if (stage === "oversize") {
      await page.locator("textarea").fill("验收预算边界。".repeat(100000));
      await page.locator("textarea").press("Enter");
      console.log("submitted oversized synthetic user input");
    } else if (stage === "edit-oversize" || stage === "recover") {
      if (stage === "recover" && !process.argv.includes("--allow-llm")) throw new Error("Recovery submits one CPA request; requires --allow-llm");
      if (stage === "recover") {
        if (!safe.model?.startsWith("custom/")) throw new Error("Recovery must use the CPA custom route");
        await page.getByRole("button", {name:/当前模型：.*cpa/i}).waitFor();
      }
      await page.getByRole("button", { name: "编辑并重发", exact: true }).last().click();
      await page.locator("textarea").fill(stage === "recover" ? "隔离验收：只回复 DESKTOP137，不调用任何工具。" : "重新验收预算边界。".repeat(100000));
      await page.locator("textarea").press("Enter");
      console.log("submitted edited synthetic input");
    } else if (stage === "reload") {
      await page.reload();
      console.log("reloaded real WebView");
    } else if (stage === "evidence") {
      const error = page.getByText(/超过.*预算|超过.*上下文|压缩.*失败|输入.*过大/).last();
      const evidence = { visibleError: await error.textContent({timeout:5000}),
        errors: safe.active_events.filter(e=>e.type==="run_error").map(e=>e.payload),
        terminals: safe.active_events.filter(e=>e.type==="run_terminal").map(e=>e.payload),
      };
      if (evidence.errors.length !== 1 || evidence.terminals.at(-1)?.status !== "failed") throw new Error("Missing durable failure");
      console.log(JSON.stringify(evidence));
      const stem = `af-137-desktop-error-${evidence.errors[0].run_id}`;
      await writeFile(resolve(tmpdir(), `${stem}.json`), JSON.stringify(evidence,null,2));
      await page.screenshot({ path: resolve(tmpdir(), `${stem}.png`) });
    } else if (stage === "success") {
      await page.getByText("DESKTOP137", { exact: true }).last().waitFor({timeout:30000});
      const evidence = { sessionId, url: page.url(), finalText: "DESKTOP137",
        events: safe.active_events.filter(e=>["run_terminal","run_error","assistant_message"].includes(e.type)).map(e=>({type:e.type,payload:e.payload})),
      };
      await writeFile(resolve(tmpdir(), "af-137-desktop-success.json"), JSON.stringify(evidence,null,2));
      await page.screenshot({path:resolve(tmpdir(),"af-137-desktop-success.png")});
      console.log("desktop success visible; evidence saved");
    } else throw new Error("Unknown desktop stage");
  }
} finally { await browser.close(); }
