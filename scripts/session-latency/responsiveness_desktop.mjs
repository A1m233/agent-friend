// Real Tauri/CDP timing and input isolation; only synthetic fixture sessions.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { readFile, writeFile, mkdir, stat, realpath, utimes } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "frontend/package.json"));
const { chromium } = require("playwright");
const args = process.argv.slice(2), opts = {};
for (let i = 0; i < args.length; i += 2) {
  assert(["--identity", "--output", "--cdp", "--invalidate-derived", "--legacy-source"].includes(args[i]) && args[i + 1]);
  opts[args[i]] = args[i + 1];
}
assert(opts["--identity"] && opts["--output"] && opts["--cdp"]);
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const identity = JSON.parse(await readFile(opts["--identity"], "utf8"));
assert(identity.synthetic_only && identity.kind === "isolated_desktop_turn_history_fixture");
assert(!opts["--invalidate-derived"] || opts["--invalidate-derived"] === "true");
const fixtures = Object.fromEntries(identity.sessions.map(s => [s.kind, s]));
const lease = JSON.parse(execFileSync("uv", ["run", "python", "-c", [
  "import json,sys", "sys.path.insert(0,sys.argv[1])",
  "from runtime_guard import runtime_state_dir,process_start_id",
  "v=json.loads((runtime_state_dir()/'lease.json').read_text(encoding='utf-8'))",
  "assert process_start_id(int(v['ownerPid'])) == v['ownerStartId']",
  "print(json.dumps(v))",
].join(";"), resolve(root, "scripts/dev")], { cwd: root, encoding: "utf8", windowsHide: true }));
assert.equal((await realpath(lease.worktree)).toLowerCase(), (await realpath(root)).toLowerCase());
assert.equal(lease.mode, "tauri");
assert.equal(lease.branch, execFileSync("git", ["branch", "--show-current"], {cwd: root, encoding: "utf8"}).trim());
const endpoint = new URL(opts["--cdp"]);
assert(["localhost", "127.0.0.1"].includes(endpoint.hostname) && lease.ports.includes(Number(endpoint.port)));
const paths = execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
  "frontend/src", "frontend/src-tauri/src", "agent/src", "agent_bridge/src", "runtime_config/src"],
  {cwd: root, encoding: "utf8"}).split("\0").filter(Boolean).filter(p => !/\.(test|spec)\.[cm]?[tj]sx?$/.test(p));
const sourceHashes = {};
for (const path of paths) {
  assert((await stat(resolve(root,path))).mtimeMs <= Date.parse(lease.startedAt), `runtime predates ${path}`);
  sourceHashes[path] = hash(await readFile(resolve(root,path)));
}
await mkdir(opts["--output"], {recursive:false});
const report = {schema:1, kind:"desktop_responsiveness", status:"running", timings:[], steps:[], requests:[],
  derived_invalidation: opts["--invalidate-derived"] === "true",
  runtime:lease, source_sha256:sourceHashes, script_sha256:hash(await readFile(fileURLToPath(import.meta.url))),
  limits:["Tauri dev WebView; no native GPU paint timing", "Warm process and uncontrolled OS cache; WebView reload between samples",
    "Delay injection steps prove UI independence; normal timing steps use real history/configuration/preparation APIs"]};
const save = () => writeFile(resolve(opts["--output"],"report.json"), JSON.stringify(report,null,2));
let browser, page;
try {
  browser = await chromium.connectOverCDP(endpoint.href, {noDefaults:true});
  const pages = browser.contexts().flatMap(c=>c.pages()).filter(p=>new URL(p.url()).pathname === "/chat.html");
  assert.equal(pages.length,1); page=pages[0]; page.setDefaultTimeout(15000);
  assert(await page.evaluate(()=>!!window.__TAURI_INTERNALS__));
  const requests=new WeakMap();
  page.on("request",r=>{if(/\/v1\/sessions(?:\/|\?|$)/.test(new URL(r.url()).pathname)) requests.set(r,Date.now());});
  page.on("requestfinished",r=>{if(!opts["--legacy-source"] && requests.has(r)) report.requests.push({path:new URL(r.url()).pathname,
    method:r.method(),started_at:requests.get(r),duration_ms:Date.now()-requests.get(r)});});
  const ids = await page.evaluate(async()=> (await (await import("/src/services/api/sessions.ts")).sessionsApi.list()).map(s=>s.session_id).sort());
  assert.deepEqual(ids, identity.sessions.map(s=>s.session_id).sort());
  await page.bringToFront();
  const open = async fixture => {
    await page.locator('[data-sidebar="menu-button"]').filter({hasText:fixture.title}).click();
  };
  const ready = async fixture => waitForAsync(async sid=> {
    const s=(await import("/src/stores/conversation.ts")).useConversationStore.getState();
    return s.currentSessionId===sid && !s.historyLoading && s.messages.length>0;
  },fixture.session_id);
  await open(fixtures.short); await ready(fixtures.short);
  if (opts["--legacy-source"]) {
    const {measureLegacy} = await import("./legacy_desktop.mjs");
    report.kind = "desktop_legacy_display";
    report.measurement_sha256 = hash(await readFile(new URL("./legacy_desktop.mjs", import.meta.url)));
    report.limits = ["Tauri dev WebView, click until target message intersects the viewport plus one animation frame",
      "Isolated journal copy, OS cache uncontrolled; short chat selected on WebView reload before each sample",
      "Sidebar metadata can initialize before click; background execution preparation enabled"];
    report.timings = await measureLegacy({page, identity, open, ready, source:opts["--legacy-source"]});
    report.status = "passed";
  } else {
  // Timestamp the actual pointerdown, then observe first correct visible message on RAF.
  for (let i=0;i<20;i++) {
    await page.reload({waitUntil:"domcontentloaded"});
    const current = i%2===0 ? fixtures.short : fixtures.long;
    const target = i%2===0 ? fixtures.long : fixtures.short;
    await ready(current);
    if(target.kind === "long" && opts["--invalidate-derived"] === "true") {
      // On our disposable synthetic data only: unchanged bytes + changed source
      // version force stale disk projections without deleting files under readers.
      await page.evaluate(async sid=> {
        await (await import("/src/services/api/sessions.ts")).sessionsApi.cancelPreparation(sid);
      },target.session_id);
      const raw=resolve(identity.data_dir,"sessions",`${target.session_id}.jsonl`);
      assert.equal(hash(await readFile(raw)),target.sha256);
      const now=new Date(); await utimes(raw,now,now);
    }
    await page.evaluate(async ({sid,uuid})=> {
      const store=(await import("/src/stores/conversation.ts")).useConversationStore;
      window.__responsivenessTiming=null;
      document.addEventListener("pointerdown",()=> {
        const start=performance.now();
        function sample() {
          const s=store.getState(), node=document.querySelector(`[data-message-id="${uuid}"]`);
          const rect=node?.getBoundingClientRect(), view=document.querySelector(".chat-message-viewport")?.getBoundingClientRect();
          if(s.currentSessionId===sid && !s.historyLoading && rect && view && rect.height>0 && rect.bottom>view.top && rect.top<view.bottom) {
            requestAnimationFrame(()=>window.__responsivenessTiming={elapsed_ms:performance.now()-start,
              user_count:s.messages.filter(m=>m.role==="user").length});
          } else if(performance.now()-start<15000) requestAnimationFrame(sample);
        }
        requestAnimationFrame(sample);
      },{once:true,capture:true});
    },{sid:target.session_id,uuid:target.expected.latest.message_uuid});
    await open(target);
    await page.waitForFunction(()=>window.__responsivenessTiming!==null);
    const timing=await page.evaluate(()=>window.__responsivenessTiming);
    assert.equal(timing.user_count,target.kind==="long"?10:2);
    report.timings.push({kind:target.kind,observed_at:Date.now(),...timing}); await save();
  }
  await page.screenshot({path:resolve(opts["--output"],"normal.png")});
  // Start with no long-session page cache. Independently delay display and preparation.
  await open(fixtures.short); await ready(fixtures.short); await page.reload({waitUntil:"domcontentloaded"}); await ready(fixtures.short);
  let releaseHistory, historyEnteredResolve;
  const historyGate=new Promise(resolve=>releaseHistory=resolve);
  const historyEntered=new Promise(resolve=>historyEnteredResolve=resolve);
  const historyPattern=`**/v1/sessions/${fixtures.long.session_id}/history?*`;
  await page.route(historyPattern,async route=>{ historyEnteredResolve(); await historyGate; await route.continue(); });
  const preparePattern=`**/v1/sessions/${fixtures.long.session_id}/prepare`;
  await page.route(preparePattern,async route=> {
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({session_id:fixtures.long.session_id,state:"preparing"})});
  });
  try {
    await open(fixtures.long); await historyEntered;
    await waitForAsync(async()=>!!(await import("/src/stores/composerSession.ts")).useComposerSessionStore.getState().configuration);
    const editor=page.locator("textarea").filter({visible:true}).first();
    await editor.fill("172 独立输入验证，保持草稿，不发送");
    assert((await editor.inputValue()).includes("172 独立输入验证"));
    const model=page.getByRole("button",{name:/当前模型：/}); assert(await model.isEnabled());
    await model.click(); await page.getByRole("menuitem",{name:/推理强度/}).waitFor(); await page.keyboard.press("Escape");
    const approval=page.getByRole("button",{name:/当前授权模式：/}); assert(await approval.isEnabled());
    await approval.click(); await page.getByRole("menuitem",{name:/每次授权/}).waitFor(); await page.keyboard.press("Escape");
    const blocked=await page.evaluate(async()=> {
      const s=(await import("/src/stores/conversation.ts")).useConversationStore.getState();
      const c=(await import("/src/stores/composerSession.ts")).useComposerSessionStore.getState();
      return {history_loading:s.historyLoading,preparation:c.phase};
    });
    assert(blocked.history_loading && blocked.preparation==="preparing");
    const send=page.locator("t-chat-sender button").last(); assert(await send.isDisabled());
    assert.equal(await page.getByRole("status").filter({hasText:/正在准备会话|可以先输入|正在读取会话设置/}).count(),0,
      "normal preparation must not add a visible loading notice");
    await page.screenshot({path:resolve(opts["--output"],"delayed-history-editable.png")});
    report.steps.push({name:"history_and_preparation_pending_input_and_controls_available",status:"passed",...blocked});
    await open(fixtures.short); await ready(fixtures.short);
    report.steps.push({name:"switch_away_while_history_pending",status:"passed"});
  } finally {
    releaseHistory(); await page.unroute(historyPattern); await page.unroute(preparePattern);
  }
  await open(fixtures.long); await ready(fixtures.long);
  await waitForAsync(async()=> (await import("/src/stores/composerSession.ts")).useComposerSessionStore.getState().phase==="ready");
  const editor=page.locator("textarea").filter({visible:true}).first();
  assert((await editor.inputValue()).includes("172 独立输入验证"));
  await editor.fill("");
  report.steps.push({name:"draft_retained_and_real_preparation_ready",status:"passed"});
  // Delay then reject an edit without issuing a real run. Exercise the native
  // sender and sidebar, including an Enter press while the request is pending.
  await open(fixtures.short); await ready(fixtures.short);
  await waitForAsync(async () => (await import("/src/stores/composerSession.ts")).useComposerSessionStore.getState().phase === "ready");
  await page.evaluate(() => {
    const fetch = window.fetch;
    const probe = window.__editProbe = {fetch, count:0, posts:[], release:null};
    const gate = new Promise(resolve => {probe.release = resolve});
    window.fetch = async (input, options) => {
      const path = new URL(input instanceof Request ? input.url : input, location.href).pathname;
      if (options?.method === "POST" && path.endsWith("/edit-resend-latest")) {
        probe.count++;
        await gate;
        return Response.json({detail:"fixture edit rejected"},{status:409});
      }
      if (options?.method === "POST" && path.startsWith("/v1/") && !path.endsWith("/prepare")) {
        probe.posts.push(path);
        return new Response(null,{status:409});
      }
      return fetch.call(window,input,options);
    };
  });
  try {
    const edit = page.getByRole("button",{name:"编辑并重发",exact:true});
    await edit.click();
    await editor.fill("提交的修改"); await editor.press("Enter");
    await waitForAsync(async sid => !!(await import("/src/stores/conversation.ts")).useConversationStore.getState().pendingEditRuns[sid], fixtures.short.session_id);
    assert(await editor.isEditable());
    await editor.fill("源会话的新草稿"); await editor.press("Enter");
    const sourceDraft = await editor.inputValue();
    assert.match(sourceDraft,/^源会话的新草稿\n?$/);
    await open(fixtures.long); await ready(fixtures.long);
    await editor.fill("另一个会话的草稿");
    await page.evaluate(() => window.__editProbe.release());
    await waitForAsync(async sid => !(await import("/src/stores/conversation.ts")).useConversationStore.getState().pendingEditRuns[sid], fixtures.short.session_id);
    assert.equal(await editor.inputValue(),"另一个会话的草稿");
    await editor.fill("");
    await open(fixtures.short); await ready(fixtures.short);
    assert.equal(await editor.inputValue(),sourceDraft);
    await edit.click();
    assert.equal(await editor.inputValue(),"提交的修改");
    await page.getByRole("button",{name:"取消编辑",exact:true}).click();
    await editor.fill("");
    const observed = await page.evaluate(() => ({count:window.__editProbe.count,posts:window.__editProbe.posts}));
    assert.deepEqual(observed,{count:1,posts:[]});
    report.steps.push({name:"pending_edit_allows_typing_navigation_and_retains_both_drafts_after_rejection",status:"passed"});
  } finally {
    await page.evaluate(() => {window.__editProbe.release(); window.fetch = window.__editProbe.fetch; delete window.__editProbe;});
  }
  report.summary=Object.fromEntries(["short","long"].map(kind=> {
    const xs=report.timings.filter(x=>x.kind===kind).map(x=>x.elapsed_ms).sort((a,b)=>a-b);
    return [kind,{samples:xs.length,median_ms:(xs[4]+xs[5])/2,p95_ms:xs[Math.ceil(xs.length*.95)-1],max_ms:xs.at(-1)}];
  }));
  report.performance_budget={controlled_slow_path_max_ms:1000,
    met:report.timings.every(sample=>sample.elapsed_ms<=1000)};
  report.status="passed";
  }
} catch(error) {
  report.status="failed"; report.error=opts["--legacy-source"] ? error.name : String(error);
  if(page && !opts["--legacy-source"]) await page.screenshot({path:resolve(opts["--output"],"failure.png")}).catch(()=>{});
} finally {
  report.finished_at=new Date().toISOString(); await save();
  if(browser) await browser.close();
}
console.log(JSON.stringify({status:report.status,summary:report.summary,error:report.error}));
process.exitCode=report.status==="passed"?0:1;

async function waitForAsync(predicate, argument, options = {}) {
  const deadline = Date.now() + (options.timeout ?? 45000);
  while (Date.now() < deadline) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error("async_condition_timeout");
}
