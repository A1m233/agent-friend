#!/usr/bin/env node
// Observe an already-running, identity-checked Tauri dev WebView. Never launch an app.
import { open, lstat } from "node:fs/promises";
import { basename, isAbsolute, resolve } from "node:path";

const LIMIT_MS = 10_000;
const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);
class SmokeFailure extends Error { constructor(code) { super(code); this.code = code; } }
const fail = (code) => { throw new SmokeFailure(code); };
const delay = (ms) => new Promise((done) => setTimeout(done, ms));

function parseArgs(argv) {
  const command = argv.shift();
  if (!["inspect", "inspect-animation", "export"].includes(command)) fail("expected-inspect-inspect-animation-or-export");
  const options = { command, cdp: "http://127.0.0.1:9222" };
  while (argv.length) {
    const key = argv.shift();
    const value = argv.shift();
    if (!["--output", "--cdp"].includes(key) || !value || value.startsWith("--")) fail("invalid-arguments");
    options[key.slice(2)] = value;
  }
  if (!options.output) fail("output-required");
  const endpoint = new URL(options.cdp);
  if (endpoint.protocol !== "http:" || !LOOPBACK.has(endpoint.hostname) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash || !["", "/"].includes(endpoint.pathname)) fail("cdp-must-be-loopback-http");
  options.cdp = endpoint.origin;
  return options;
}

function isDevUrl(value, pathname) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" && LOOPBACK.has(url.hostname) && url.port === "1420" &&
      url.pathname === pathname && !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

async function targets(endpoint) {
  const response = await fetch(`${endpoint}/json/list`, { signal: AbortSignal.timeout(LIMIT_MS) });
  if (!response.ok || !response.body) fail("cdp-target-list-unavailable");
  const reader = response.body.getReader();
  const chunks = [];
  let bytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > 1024 * 1024) { await reader.cancel(); fail("cdp-target-list-too-large"); }
    chunks.push(Buffer.from(value));
  }
  const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!Array.isArray(result) || result.length > 128) fail("invalid-cdp-target-list");
  return result;
}

async function connect(target, endpoint) {
  const url = new URL(target.webSocketDebuggerUrl);
  if (url.protocol !== "ws:" || !LOOPBACK.has(url.hostname) || url.port !== new URL(endpoint).port || url.username || url.password || url.search || url.hash) fail("unsafe-cdp-websocket");
  const socket = new WebSocket(url);
  await new Promise((done, reject) => {
    const timeout = setTimeout(() => { socket.close(); reject(new SmokeFailure("cdp-connect-timeout")); }, LIMIT_MS);
    socket.addEventListener("open", () => { clearTimeout(timeout); done(); }, { once: true });
    socket.addEventListener("error", () => { clearTimeout(timeout); reject(new SmokeFailure("cdp-connect-failed")); }, { once: true });
  });
  let next = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    if (typeof data !== "string" || data.length > 1024 * 1024) return;
    let message;
    try { message = JSON.parse(data); } catch { return; }
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timeout);
    if (message.error) request.reject(new SmokeFailure("cdp-command-failed"));
    else request.done(message.result);
  });
  socket.addEventListener("close", () => {
    for (const request of pending.values()) { clearTimeout(request.timeout); request.reject(new SmokeFailure("cdp-disconnected")); }
    pending.clear();
  });
  return {
    send(method, params = {}, timeoutMs = LIMIT_MS) {
      return new Promise((done, reject) => {
        const id = ++next;
        const timeout = setTimeout(() => { pending.delete(id); reject(new SmokeFailure("cdp-command-timeout")); }, Math.min(LIMIT_MS, timeoutMs));
        pending.set(id, { done, reject, timeout });
        try { socket.send(JSON.stringify({ id, method, params })); }
        catch { clearTimeout(timeout); pending.delete(id); reject(new SmokeFailure("cdp-send-failed")); }
      });
    },
    close() { socket.close(); },
  };
}

// Every expression returns only an allowlisted result, never the page body or conversation content.
const identityExpression = `(() => ({
  label: window.__TAURI_INTERNALS__?.metadata?.currentWindow?.label ?? null,
  tauri: typeof window.__TAURI_INTERNALS__?.invoke === "function",
  visibility: document.visibilityState,
  url: location.href
}))()`;

async function evaluate(client, expression, timeout = LIMIT_MS) {
  const result = await client.send("Runtime.evaluate", { expression, returnByValue: true }, timeout);
  if (result.exceptionDetails || !result.result || !("value" in result.result)) fail("webview-read-failed");
  return result.result.value;
}

async function identity(client, label, pathname) {
  const value = await evaluate(client, identityExpression);
  if (!value.tauri || value.label !== label || !isDevUrl(value.url, pathname)) fail("not-expected-tauri-dev-window");
  if (label === "chat" && value.visibility !== "visible") fail("workspace-window-not-visible-open-it-manually");
  return { label: value.label, targetUrl: value.url, documentVisibility: value.visibility };
}

const inspectExpression = `(() => {
  const finite = n => Number.isFinite(n) && Math.abs(n) <= 1000000 ? n : null;
  const rect = value => value && [value.x,value.y,value.w ?? value.width,value.h ?? value.height].every(Number.isFinite)
    ? {x:finite(value.x),y:finite(value.y),width:finite(value.w ?? value.width),height:finite(value.h ?? value.height)} : null;
  const anchor = window.__AGENT_FRIEND_ANCHOR_DIAGNOSTICS__;
  const sprite = rect(anchor?.spriteScreen);
  let hit = null;
  if (sprite) {
    const x = sprite.x + sprite.width / 2, y = sprite.y + sprite.height / 2;
    const element = document.elementFromPoint(x,y);
    hit = {x,y,elementPresent:!!element,dataHit:!!element?.closest("[data-hit]"),
      pointerEvents:element ? getComputedStyle(element).pointerEvents : null};
  }
  return {actualDpr:finite(devicePixelRatio),viewport:{width:finite(innerWidth),height:finite(innerHeight)},
    displayDpr:finite(anchor?.dpr),spriteBounds:sprite,overlayViewport:rect(anchor?.overlayViewport),
    actionBarBounds:rect(anchor?.actionBarRect),
    canvas:Array.from(document.querySelectorAll("canvas")).slice(0,2).map(element=>({width:finite(element.width),height:finite(element.height),bounds:rect(element.getBoundingClientRect())})),
    bodyPointerEvents:getComputedStyle(document.body).pointerEvents,spriteCenterDomHit:hit};
})()`;

// Existing renderer objects only: no imports, new rAF, ticker API, render, or state mutation.
// Canvas PNG exists only transiently inside this evaluation; only its digest crosses CDP.
const animationExpression = `(async () => {
  const number = value => Number.isFinite(value) ? value : null;
  const canvases = Array.from(document.querySelectorAll("canvas"));
  const result = {now:performance.now(),timeOrigin:performance.timeOrigin,visibility:document.visibilityState,
    canvasCount:canvases.length,tickers:[],canvasSha256:null,canvasPngBytes:null,canvasHashStatus:"unavailable"};
  if(canvases.length!==1) return result;
  const canvas=canvases[0];
  const host=canvas.parentElement;
  const key=host && Object.keys(host).find(name=>name.startsWith("__reactFiber$"));
  let fiber=key?host[key]:null;
  for(let depth=0;fiber && depth<24;depth++,fiber=fiber.return) {
    const name=fiber.type?.displayName ?? fiber.type?.name;
    if(!["Spine38AvatarSurface","Live2DAvatarSurface"].includes(name)) continue;
    let hook=fiber.memoizedState;
    for(let index=0;hook && index<96;index++,hook=hook.next) {
      const state=hook.memoizedState;
      const candidate=state?.current ?? state;
      if(!candidate || typeof candidate!=="object") continue;
      const ticker=Object.getOwnPropertyDescriptor(candidate,"_ticker")?.value;
      const renderer=Object.getOwnPropertyDescriptor(candidate,"renderer")?.value;
      if(!ticker || !renderer || renderer.view!==canvas) continue;
      const slot=candidate.stage?.children?.[0];
      result.tickers.push({component:name,started:typeof ticker.started==="boolean"?ticker.started:null,
        requestIdPrivate:number(ticker._requestId),lastTime:number(ticker.lastTime),
        deltaMS:number(ticker.deltaMS),elapsedMS:number(ticker.elapsedMS),speed:number(ticker.speed),
        listeners:number(ticker.count),contextLost:renderer.gl?renderer.gl.isContextLost():null,
        slot:slot?{x:number(slot.x),y:number(slot.y)}:null});
      break;
    }
    break;
  }
  result.canvasSize={width:canvas.width,height:canvas.height};
  if(canvas.width*canvas.height>16777216) {result.canvasHashStatus="pixel-budget-exceeded";return result;}
  try {
    const encoded=canvas.toDataURL("image/png");
    if(!encoded.startsWith("data:image/png;base64,")) {result.canvasHashStatus="png-unavailable";return result;}
    const base64=encoded.slice(encoded.indexOf(",")+1);
    if(base64.length>12*1024*1024) {result.canvasHashStatus="png-budget-exceeded";return result;}
    const binary=atob(base64);
    const bytes=Uint8Array.from(binary,character=>character.charCodeAt(0));
    const digest=await crypto.subtle.digest("SHA-256",bytes);
    result.canvasPngBytes=bytes.length;
    result.canvasSha256=Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,"0")).join("");
    result.canvasHashStatus="included-digest-only";
  } catch {result.canvasHashStatus="read-unavailable";}
  return result;
})()`;

async function inspectAnimation(client,report) {
  const read=async () => {
    await identity(client,"pet","/pet.html");
    const response=await client.send("Runtime.evaluate",{expression:animationExpression,returnByValue:true,awaitPromise:true});
    if(response.exceptionDetails || !response.result?.value) fail("animation-read-failed");
    return response.result.value;
  };
  const first=await read();
  await delay(2000);
  const second=await read();
  const samePage=first.timeOrigin===second.timeOrigin;
  report.animation={samples:[first,second],samePage,requestedIntervalMs:2000,
    elapsedMs:samePage?second.now-first.now:null,
    tickerTimeAdvanced:samePage&&first.tickers.length===1&&second.tickers.length===1&&
      first.tickers[0].component===second.tickers[0].component&&
      first.tickers[0].lastTime!==null&&second.tickers[0].lastTime!==null
      ?second.tickers[0].lastTime>first.tickers[0].lastTime:null,
    canvasHashChanged:samePage&&first.canvasSha256&&second.canvasSha256?first.canvasSha256!==second.canvasSha256:null,
    savesImages:false,registersAnimationFrame:false,
    limitations:["React Fiber and _ticker/_requestId are private read-only inspection paths, not stable product APIs.",
      "Two samples describe this interval only. An unchanged canvas hash alone does not prove a frozen animation.",
      "Reading the existing canvas for hashing may incur a GPU readback; no render or ticker recovery is invoked."]};
}

// Selectors/text correspond to HistorySidebar, SettingsApp and FeedbackDiagnosticsPanel.
// Existence, visibility, geometry and topmost hit are re-read from the live DOM before each click.
const domHelpers = `
  const text = element => (element.textContent ?? "").trim();
  const shown = element => !!element && element.getClientRects().length > 0 && getComputedStyle(element).visibility === "visible";
  const heading = Array.from(document.querySelectorAll("h2")).filter(element => text(element) === "帮助与反馈" && shown(element));
  const panel = heading.length === 1 ? heading[0].parentElement?.parentElement : null;
  const buttons = key => {
    const scope = key === "settings" ? document.querySelectorAll('[data-sidebar="header"] [data-sidebar="menu-button"]')
      : key === "feedback" ? document.querySelectorAll('[data-sidebar="footer"] [data-sidebar="menu-button"]')
      : panel?.querySelectorAll('button[data-slot="button"]') ?? [];
    const label = key === "settings" ? "设置" : key === "feedback" ? "帮助与反馈" : "生成诊断包";
    return Array.from(scope).filter(element => text(element) === label && shown(element));
  };
`;

function buttonExpression(key) {
  return `(() => {${domHelpers}
    const matches = buttons(${JSON.stringify(key)});
    if(matches.length !== 1) return {count:matches.length};
    const button = matches[0], r = button.getBoundingClientRect();
    const x = r.left+r.width/2, y = r.top+r.height/2;
    let scroll = button.parentElement;
    while(scroll && !(scroll.scrollHeight>scroll.clientHeight && ["auto","scroll"].includes(getComputedStyle(scroll).overflowY))) scroll=scroll.parentElement;
    const sr=scroll?.getBoundingClientRect();
    const sx=sr ? Math.max(1,Math.min(innerWidth-1,sr.left+sr.width/2)) : null;
    const sy=sr ? Math.max(1,Math.min(innerHeight-1,sr.top+sr.height/2)) : null;
    const top=document.elementFromPoint(x,y);
    const scrollTop=sr ? document.elementFromPoint(sx,sy) : null;
    return {count:1,disabled:button.disabled || button.getAttribute("aria-busy")==="true",
      rect:{x:r.x,y:r.y,width:r.width,height:r.height},point:{x,y},
      hit:!!top && (top===button || button.contains(top)),
      inViewport:x>=0&&x<innerWidth&&y>=0&&y<innerHeight,
      scroll:sr&&scrollTop&&scroll.contains(scrollTop)?{x:sx,y:sy,deltaY:y<sr.top?-600:600}:null};
  })()`;
}

const panelExpression = `(() => {${domHelpers}
  if(!panel) return {present:false};
  const sessionChecks=Array.from(panel.querySelectorAll('[role="checkbox"][aria-label^="选择会话 "]'));
  const checked=element=>element?.getAttribute("aria-checked")==="true" || element?.getAttribute("data-state")==="checked";
  const render=panel.querySelector('[role="checkbox"][aria-label="在诊断包中包含最近一次渲染证据"]');
  const attachment=panel.querySelector('[role="checkbox"][aria-label="包含相关原始附件"]');
  const description=panel.querySelector('textarea[aria-label="问题说明"]');
  const paragraphs=Array.from(panel.querySelectorAll("p"));
  const result=paragraphs.filter(element=>["诊断包已生成","诊断包已生成，但部分内容获取失败"].includes(text(element)));
  const status=result.length===1?text(result[0]):null;
  const path=result.length===1?result[0].parentElement?.querySelector("p.break-all"):null;
  return {present:true,selectedSessionCount:sessionChecks.filter(checked).length,
    loading:paragraphs.some(element=>text(element)==="正在加载会话…"),
    descriptionEmpty:!!description && description.value.length===0,
    includeAttachmentOriginals:checked(attachment),manualRenderEvidence:checked(render)&&!render.disabled,
    automaticRenderEvidenceIncluded:checked(render)&&render.disabled,
    renderControlPresent:!!render,
    result:status&&path?{outcome:status==="诊断包已生成"?"complete":"partial",path:text(path)}:null};
})()`;

async function readyPanel(client) {
  const deadline = Date.now() + LIMIT_MS;
  while (Date.now() < deadline) {
    const panel = await evaluate(client, panelExpression, deadline-Date.now());
    if (panel.present && !panel.loading) return panel;
    await delay(200);
  }
  fail("feedback-panel-not-ready");
}

function assertEmptyConsent(panel) {
  if (!panel.present || !panel.descriptionEmpty || panel.selectedSessionCount !== 0 || panel.includeAttachmentOriginals || panel.manualRenderEvidence) fail("feedback-has-private-selection-or-description");
}

async function clickButton(client, key, report) {
  const deadline = Date.now() + LIMIT_MS;
  let probe;
  for (let attempt=0; attempt<8 && Date.now()<deadline; attempt++) {
    probe=await evaluate(client,buttonExpression(key),deadline-Date.now());
    if(probe.count===1 && !probe.disabled && probe.inViewport && probe.hit) break;
    if(probe.count===1 && !probe.disabled && !probe.inViewport && probe.scroll) {
      await client.send("Input.dispatchMouseEvent",{type:"mouseWheel",...probe.scroll,deltaX:0},deadline-Date.now());
    }
    await delay(200);
  }
  report.lastButtonProbe={button:key,...probe};
  if(probe?.count!==1 || probe.disabled || !probe.inViewport || !probe.hit) fail(`button-not-safely-clickable-${key}`);
  await identity(client,"chat","/chat.html");
  if(key==="generate") assertEmptyConsent(await readyPanel(client));
  probe=await evaluate(client,buttonExpression(key));
  if(probe.count!==1 || probe.disabled || !probe.inViewport || !probe.hit) fail(`button-changed-before-click-${key}`);
  const point={x:probe.point.x,y:probe.point.y,button:"left",clickCount:1};
  // Real WebView input, not DOM click() and not an invocation of the export command.
  if(key==="generate") report.generationClickAttempted=true;
  await client.send("Input.dispatchMouseEvent",{type:"mousePressed",...point});
  await client.send("Input.dispatchMouseEvent",{type:"mouseReleased",...point});
  report.actions.push({button:key,rect:probe.rect,point:probe.point,clickedAt:new Date().toISOString()});
}

async function exportFromFeedback(client, report) {
  await clickButton(client,"settings",report);
  await clickButton(client,"feedback",report);
  const before=await readyPanel(client);
  assertEmptyConsent(before);
  report.consent={selectedSessionCount:0,includeAttachmentOriginals:false,manualRenderEvidence:false,
    automaticRenderEvidenceIncluded:before.automaticRenderEvidenceIncluded,renderControlPresent:before.renderControlPresent};
  report.exportRequestedAt=new Date().toISOString();
  await clickButton(client,"generate",report);
  const deadline=Date.now()+60_000;
  while(Date.now()<deadline) {
    const current=await evaluate(client,panelExpression,Math.min(LIMIT_MS,deadline-Date.now()));
    if(current.result && current.result.path!==before.result?.path) {
      const path=current.result.path;
      if(!isAbsolute(path) || !/^agent-friend-diagnostics-[a-zA-Z0-9-]+\.zip$/.test(basename(path))) fail("unexpected-generated-zip-path");
      const file=await lstat(path);
      if(!file.isFile() || file.isSymbolicLink() || file.size===0) fail("generated-zip-not-a-regular-file");
      report.result={...current.result,bytes:file.size,verifiedAt:new Date().toISOString()};
      report.consent.automaticRenderEvidenceIncluded=current.automaticRenderEvidenceIncluded;
      return;
    }
    await delay(250);
  }
  fail("export-final-ui-timeout-do-not-reclick");
}

async function main() {
  const options=parseArgs(process.argv.slice(2));
  const output=resolve(options.output);
  // Reserve before CDP activity: an existing evidence file must never be overwritten.
  const file=await open(output,"wx");
  const report={schemaVersion:1,command:options.command,startedAt:new Date().toISOString(),status:"failed",actions:[],
    evidenceLevel:options.command==="export"?"desktop interaction + generated ZIP existence":"desktop DOM via CDP",
    runtimeIdentityCaveat:"Caller must separately verify the dev lease, worktree and loaded source freshness.",
    capturesScreenshot:false,readsConversationContent:false};
  let client;
  try {
    const pathname=options.command==="export"?"/chat.html":"/pet.html";
    const available=await targets(options.cdp);
    const found=available.filter(target=>target.type==="page" && isDevUrl(target.url,pathname));
    if(found.length!==1) fail("expected-one-loopback-dev-target");
    client=await connect(found[0],options.cdp);
    report.target=await identity(client,options.command==="export"?"chat":"pet",pathname);
    if(options.command==="inspect") report.facts=await evaluate(client,inspectExpression);
    else if(options.command==="inspect-animation") await inspectAnimation(client,report);
    else await exportFromFeedback(client,report);
    report.status="complete";
  } catch(error) {
    report.failure=error instanceof SmokeFailure?error.code:"local-or-cdp-operation-failed";
    process.exitCode=1;
  } finally {
    client?.close();
    report.completedAt=new Date().toISOString();
    await file.writeFile(`${JSON.stringify(report,null,2)}\n`,"utf8");
    await file.close();
  }
  console.log(JSON.stringify({status:report.status,output,failure:report.failure,result:report.result,
    animation:report.animation?{tickerTimeAdvanced:report.animation.tickerTimeAdvanced,
      canvasHashChanged:report.animation.canvasHashChanged}:undefined}));
}

main().catch(error=>{console.error(error instanceof SmokeFailure?error.code:"smoke-preflight-or-output-failed");process.exitCode=1;});
