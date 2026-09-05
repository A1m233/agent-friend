// One-off installed-WebView diagnostic; no emulation and no application source changes.
import { writeFile } from 'node:fs/promises';

const mode = process.argv[2] ?? 'reload';
if (!['reload', 'transport'].includes(mode)) throw Error('Expected reload or transport');
const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const target = targets.find(t => new URL(t.url).pathname === '/pet.html');
if (!target) throw Error('No installed pet WebView');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let seq = 0;
const pending = new Map();
const requests = new Map();
const report = { mode, startedAt: new Date().toISOString(), targetUrl: target.url, network: [], results: [] };
let round = 'baseline';
socket.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id) {
    const handler = pending.get(m.id);
    pending.delete(m.id);
    if (handler) m.error ? handler.reject(m.error) : handler.resolve(m.result);
    return;
  }
  const p = m.params;
  if (m.method === 'Network.requestWillBeSent' && p.request.url.includes('/set_pet_webview_dpr')) {
    let dpr;
    try { dpr = JSON.parse(p.request.postData).dpr; } catch { dpr = 'body-unavailable'; }
    // Never retain headers (which include the invoke key) or unrelated requests.
    const entry = { round, id: p.requestId, method: p.request.method, sentAt: p.timestamp, dpr };
    requests.set(p.requestId, entry);
    report.network.push(entry);
  } else if (requests.has(p?.requestId)) {
    const entry = requests.get(p.requestId);
    if (m.method === 'Network.responseReceived') Object.assign(entry, { responseAt: p.timestamp, status: p.response.status });
    if (m.method === 'Network.loadingFinished') entry.finishedAt = p.timestamp;
    if (m.method === 'Network.loadingFailed') entry.error = p.errorText;
  }
};
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression });
  if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}

const sampleBody = `
  const api = window.__TAURI_INTERNALS__;
  const position = await api.invoke('plugin:window|outer_position', {label:'pet'});
  let payload, count = 0;
  const handler = api.transformCallback(e => {payload = e.payload; count++;});
  const eventId = await api.invoke('plugin:event|listen', {event:'pet://cursor', target:{kind:'Any'}, handler});
  try {
    let stable = null;
    for (let attempt = 0; attempt < 8; attempt++) {
      const before = await api.invoke('plugin:window|cursor_position');
      const priorCount = count;
      await new Promise(r => setTimeout(r, 80));
      const current = payload;
      const after = await api.invoke('plugin:window|cursor_position');
      if (count > priorCount && before.x === after.x && before.y === after.y) {
        const dx = after.x - position.x, dy = after.y - position.y;
        stable = {physicalCursor:after, payload:current, inferredX:Math.abs(current.x)>1?dx/current.x:null, inferredY:Math.abs(current.y)>1?dy/current.y:null};
        break;
      }
    }
    const canvas = document.querySelector('canvas');
    return {devicePixelRatio, viewport:{width:innerWidth,height:innerHeight}, canvas:canvas?{width:canvas.width,height:canvas.height}:null, position, sample:stable};
  } finally {
    window.__TAURI_EVENT_PLUGIN_INTERNALS__.unregisterListener('pet://cursor', eventId);
    await api.invoke('plugin:event|unlisten', {event:'pet://cursor', eventId});
    api.unregisterCallback(handler);
  }
`;
const sample = () => evaluate(`(async()=>{${sampleBody}})()`);
try {
  await send('Network.enable');
  await send('Page.enable');
  report.results.push({ round, value: await sample() });
  if (mode === 'reload') {
    for (let i = 1; i <= 3; i++) {
      round = `reload-${i}`;
      await send('Page.reload', {ignoreCache:false});
      await new Promise(r => setTimeout(r, 2500));
      report.results.push({round, value:await sample()});
    }
  } else {
    report.controlledInput = 'Actual IPC: synthetic DPR reports 1.75 then actual DPR. No delay or transport interception.';
    for (const kind of ['sequential', 'concurrent']) {
      for (let i = 1; i <= 10; i++) {
        round = `${kind}-${i}`;
        const calls = await evaluate(`(async()=>{
          const api = window.__TAURI_INTERNALS__, actual = devicePixelRatio;
          await api.invoke('set_pet_webview_dpr', {dpr:actual});
          const outcomes = [], begin = performance.now();
          const call = (dpr, order) => api.invoke('set_pet_webview_dpr', {dpr}).then(
            () => outcomes.push({order,dpr,status:'fulfilled',elapsed:performance.now()-begin}),
            error => outcomes.push({order,dpr,status:'rejected',error:String(error),elapsed:performance.now()-begin})
          );
          if (${JSON.stringify(kind)} === 'sequential') {await call(1.75,1);await call(actual,2);}
          else await Promise.all([call(1.75,1),call(actual,2)]);
          return {actual,outcomes};
        })()`);
        report.results.push({round, calls, value:await sample()});
      }
    }
  }
} catch (error) {
  report.error = String(error?.stack ?? error);
} finally {
  if (mode === 'transport') {
    round = 'restore';
    try {
      await evaluate(`window.__TAURI_INTERNALS__.invoke('set_pet_webview_dpr', {dpr:devicePixelRatio})`);
      report.restored = await sample();
    } catch (error) { report.restoreError = String(error); }
  }
  report.finishedAt = new Date().toISOString();
  const path = `.tmp/pet-dpr-${mode}-${report.startedAt.replace(/[:.]/g,'-')}.json`;
  await writeFile(path, JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({path,...report}, null, 2));
  socket.close();
  if (report.error || report.restoreError) process.exitCode = 1;
}
