// Real Tauri WebView only. This probe never submits a user message or resumes activity.
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const require = createRequire(resolve("frontend/package.json"));
const { chromium } = require("playwright");
const [stage, output] = process.argv.slice(2);
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", { noDefaults: true });
const endpoint = "http://127.0.0.1:18800";
async function status() {
  const response = await fetch(`${endpoint}/v1/companionship`);
  if (!response.ok) throw new Error(`status ${response.status}`);
  return response.json();
}
async function until(read, predicate) {
  for (let i=0;i<100;i++) {
    const value=await read();
    if (predicate(value)) return value;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error("state did not converge");
}
const pages = () => browser.contexts().flatMap(context=>context.pages());
try {
  if (stage === "open") {
    const page=pages().find(p=>p.url().includes("pet.html") || p.url().includes("chat.html"));
    if (!page) throw new Error("No actual Tauri WebView");
    await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke("open_settings",{request:{target:"general"}}));
    console.log(JSON.stringify({opened:"general",pages:pages().map(p=>p.url())}));
  } else {
    const page=pages().find(p=>p.url().includes("chat.html"));
    if (!page) throw new Error("No actual Tauri chat WebView");
    if (stage === "reload") {
      const errors=[];
      page.on("pageerror",error=>errors.push(String(error)));
      await page.reload();
      await page.getByRole("button",{name:"设置",exact:true}).waitFor({timeout:15000});
      console.log(JSON.stringify({reloaded:true,errors}));
    } else if (stage === "inspect") {
      console.log(JSON.stringify({url:page.url(),title:await page.title(),
        controls:await page.locator('button,input,[role="switch"],[role="combobox"]').evaluateAll(es=>es.map(e=>({role:e.getAttribute("role"),label:e.getAttribute("aria-label"),text:e.innerText,placeholder:e.getAttribute("placeholder")})))}));
    } else if (stage === "preferences") {
      if (!output) throw new Error("new output directory required");
      const root = resolve(output);
      await mkdir(root);
      const before = await status();
      const openTab = async () => {
        await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("open_settings", {request:{target:"companionship"}}));
        await page.getByRole("heading", {name:"自主陪伴",exact:true}).waitFor();
      };
      await openTab();
      const interval = page.getByRole("spinbutton", {name:"最长唤醒间隔（分钟）"});
      const value = Number(await interval.inputValue());
      const candidate = value === 10080 ? value - 1 : value + 1;
      if (await page.getByRole("button",{name:/^(暂停|恢复)(活动|主动联系)$/}).count()) throw new Error("obsolete pause controls present");
      if (await page.getByRole("spinbutton").count() !== 2 || await page.getByRole("button",{name:/保存运行限制/}).count())
        throw new Error("obsolete limits present");
      try {
        await interval.fill(String(candidate));
        await interval.press("Tab");
        await until(status, s => s.limits.interval_minutes === candidate);
        await page.reload();
        await openTab();
        if (Number(await interval.inputValue()) !== candidate) throw new Error("interval did not survive reload");
      } finally {
        await openTab();
        await interval.fill(String(value));
        await interval.press("Enter");
        await until(status, s => s.limits.interval_minutes === value);
      }
      const after = await status();
      await page.screenshot({path:resolve(root,"companionship.png")});
      await writeFile(resolve(root,"result.json"), JSON.stringify({initialEnabled:before.enabled,
        initialInterval:value, editedInterval:candidate, restoredInterval:after.limits.interval_minutes,
        controls:Object.keys(after.limits), innerIdentityStable:before.inner_session_id===after.inner_session_id}, null, 2));
      console.log("Verified independent settings tab, auto-persist, reload and restoration.");
    } else if (stage === "verify") {
      if (!output) throw new Error("new output directory required");
      const root=resolve(output);
      await mkdir(root,{recursive:false});
      const before=await status();
      if (!before.activity_paused || before.active_run_id) throw new Error("requires paused isolated fixture");
      const fixture=await fetch(`${endpoint}/v1/sessions/acceptance-169`).then(r=>r.json());
      if (!fixture.session_id || !JSON.stringify(fixture).includes("desktop-fixture")) throw new Error("not the synthetic fixture");
      await page.getByRole("button",{name:"自主陪伴",exact:true}).click();
      const mode=page.getByRole("combobox",{name:"授权模式"});
      await mode.click();
      const options=await page.getByRole("option").allTextContents();
      if (options.length!==2 || !options.includes("帮我审批") || !options.includes("完全授权")) throw new Error(`wrong modes ${options}`);
      await page.keyboard.press("Escape");
      const toggle=page.getByRole("switch",{name:"开启自主陪伴"});
      if (!before.enabled) await toggle.click();
      const enabled=await until(status,s=>s.enabled&&s.inner_session_id);
      if (!enabled.activity_paused || enabled.active_run_id) throw new Error("enable unexpectedly resumed paused activity");
      if (await page.getByRole("button",{name:/^(暂停|恢复)(活动|主动联系)$/}).count()) throw new Error("obsolete pause controls present");
      await page.screenshot({path:resolve(root,"settings.png")});
      await page.getByRole("button",{name:"查看活动",exact:true}).click();
      await page.getByText(/内心活动 · 只读/).waitFor({timeout:15000});
      if (await page.locator("textarea:visible").count()) throw new Error("inner view exposes composer");
      await page.screenshot({path:resolve(root,"inner-readonly.png")});
      await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke("open_chat_session",{sessionId:"acceptance-169"}));
      await page.getByText("主动消息",{exact:true}).waitFor({timeout:15000});
      await page.getByText("给那只怕黑却喜欢星星的猫：可以怕黑，也可以喜欢星星。",{exact:true}).waitFor();
      await page.screenshot({path:resolve(root,"proactive-message.png")});
      await page.reload();
      await page.getByText("主动消息",{exact:true}).waitFor({timeout:15000});
      await page.screenshot({path:resolve(root,"proactive-reload.png")});
      await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke("open_settings",{request:{target:"general"}}));
      await page.getByRole("button",{name:"自主陪伴",exact:true}).click();
      await page.getByRole("switch",{name:"开启自主陪伴"}).click();
      const after=await until(status,s=>!s.enabled);
      if (after.today_model_calls!==before.today_model_calls) throw new Error("unexpected model call during read-only inspection");
      const record={surface:"real Tauri WebView2",url:page.url(),options,before,enabled,after,
        verified:["two-companionship-modes","enable-preserves-pause","pause-controls-hidden","inner-readonly","prepared-source-visible","reload-retains-source","disable"],
        scope:"desktop controls plus actual durable business state; prepared-message fixture; no real LLM or IM"};
      await writeFile(resolve(root,"result.json"),JSON.stringify(record,null,2));
      console.log(JSON.stringify(record));
    } else throw new Error("unknown desktop stage");
  }
} finally { await browser.close(); }
