// Explicitly authorized two-turn smoke on a newly created isolated test chat.
import { createRequire } from "node:module";
import { readFile,writeFile,mkdir } from "node:fs/promises";
import { resolve,dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID,createHash } from "node:crypto";
import assert from "node:assert/strict";
const root=resolve(dirname(fileURLToPath(import.meta.url)),"../..");
const {chromium}=createRequire(resolve(root,"frontend/package.json"))("playwright");
const [identityPath,output,authorization]=process.argv.slice(2);
assert.equal(authorization,"--background-calls-authorized");
const fixture=JSON.parse(await readFile(identityPath,"utf8"));
assert(fixture.synthetic_only);
await mkdir(output,{recursive:false});
const sid=randomUUID(),title=`172 smoke ${sid.slice(0,8)}`;
const report={kind:"desktop_two_turn_send_smoke",session_id:sid,status:"running",turns:[],
  limits:["Uses configured desktop model and permits normal post-turn memory hooks", "No retry of a send", "Cold lease reuse separately covered by deterministic tests"]};
const save=()=>writeFile(resolve(output,"report.json"),JSON.stringify(report,null,2));
let browser,page;
try {
  browser=await chromium.connectOverCDP("http://127.0.0.1:9222",{noDefaults:true});
  page=browser.contexts().flatMap(c=>c.pages()).find(p=>new URL(p.url()).pathname==="/chat.html");
  assert(page && await page.evaluate(()=>!!window.__TAURI_INTERNALS__)); page.setDefaultTimeout(20000);
  const ids=await page.evaluate(async()=> (await (await import("/src/services/api/sessions.ts")).sessionsApi.list()).map(s=>s.session_id).sort());
  assert.deepEqual(ids,fixture.sessions.map(s=>s.session_id).sort());
  assert.equal((await page.evaluate(async()=> (await (await fetch("/v1/companionship")).json()).enabled)),false);
  await page.getByRole("button",{name:"新对话",exact:true}).first().click();
  await waitForAsync(async()=> {
    const s=(await import("/src/stores/conversation.ts")).useConversationStore.getState();
    return s.currentSessionId===null && !!s.currentModel;
  });
  const created=await page.evaluate(async ({sid,title})=> {
    const s=(await import("/src/stores/conversation.ts")).useConversationStore.getState();
    const response=await fetch("/v1/sessions",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({creation_id:sid,title,model:s.currentModel,reasoningPreset:s.currentReasoningPreset})});
    if(!response.ok) throw new Error(`create ${response.status}`);
    return response.json();
  },{sid,title});
  report.model_ref=createHash("sha256").update(created.model).digest("hex").slice(0,16);
  const select=async()=> {
    await page.locator('[data-sidebar="menu-button"]').filter({hasText:title}).click();
    await waitForAsync(async id=> {
      const s=(await import("/src/stores/composerSession.ts")).useComposerSessionStore.getState();
      return s.sessionId===id && s.phase==="ready";
    },sid);
  };
  await select();
  for(let turn=1;turn<=2;turn++) {
    if(turn===2) {
      await page.locator('[data-sidebar="menu-button"]').filter({hasText:fixture.sessions.find(s=>s.kind==="short").title}).click();
      await select();
    }
    const marker=`AF172_OK_${turn}`;
    const before=await page.evaluate(async()=>Object.keys((await import("/src/stores/conversation.ts")).useConversationStore.getState().runSnapshots));
    const editor=page.locator("textarea").filter({visible:true}).first();
    await editor.fill(`本轮是程序验收。不要调用工具、发通知或读写文件。只回复 ${marker}。`);
    const started=Date.now(); await editor.press("Enter");
    await waitForAsync(async previous=>Object.keys((await import("/src/stores/conversation.ts")).useConversationStore.getState().runSnapshots).some(id=>!previous.includes(id)),before);
    const runId=await page.evaluate(async previous=>Object.keys((await import("/src/stores/conversation.ts")).useConversationStore.getState().runSnapshots).find(id=>!previous.includes(id)),before);
    const entry={turn,run_id:runId,accepted_ms:Date.now()-started}; report.turns.push(entry); await save();
    await waitForAsync(async id=>(await import("/src/stores/conversation.ts")).useConversationStore.getState().runSnapshots[id]?.status==="terminal",runId,{timeout:180000});
    entry.total_ms=Date.now()-started;
    entry.run=await page.evaluate(async id=>(await (await fetch(`/v1/runs/${id}`)).json()),runId);
    assert.equal(entry.run.outcome,"completed");
    await waitForAsync(async ({id,marker})=> {
      const s=(await import("/src/stores/conversation.ts")).useConversationStore.getState();
      return s.messages.some(m=>m.role==="assistant" && m.runId===id && m.blocks.some(b=>b.kind==="text" && b.text.includes(marker)));
    },{id:runId,marker});
    const visible=page.locator("[data-message-id]").filter({hasText:marker}).last();
    await visible.scrollIntoViewIfNeeded(); assert(await visible.isVisible());
    entry.visible=true;
    await page.screenshot({path:resolve(output,`turn-${turn}.png`)}); await save();
  }
  const raw=await readFile(resolve(fixture.data_dir,"sessions",`${sid}.jsonl`),"utf8");
  const events=raw.trim().split("\n").map(line=>JSON.parse(line));
  report.persistence={users:events.filter(e=>e.type==="user_message").length,
    matching_assistants:events.filter(e=>e.type==="assistant_message" && /AF172_OK_[12]/.test(e.payload.content??"")).length,
    tools:events.filter(e=>e.type==="tool_call_request").length,
    sha256:createHash("sha256").update(raw).digest("hex")};
  assert.equal(report.persistence.users,2); assert.equal(report.persistence.matching_assistants,2); assert.equal(report.persistence.tools,0);
  report.status="passed";
} catch(error) { report.status="failed";report.error=String(error); }
finally {
  report.finished_at=new Date().toISOString();await save();if(browser) await browser.close();
}
console.log(JSON.stringify({status:report.status,error:report.error,session_id:sid,turns:report.turns.length}));
process.exitCode=report.status==="passed"?0:1;

async function waitForAsync(predicate, argument, options = {}) {
  const deadline = Date.now() + (options.timeout ?? 45000);
  while (Date.now() < deadline) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error("async_condition_timeout");
}
