// Measure an explicitly selected journal in an isolated desktop profile. Never
// collect its text, screenshots or title. Backend preparation runs as in the app.
import assert from "node:assert/strict";
import { constants, createReadStream } from "node:fs";
import { copyFile, realpath, stat } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";

async function digest(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

export async function measureLegacy({page, identity, open, ready, source}) {
  const short = identity.sessions.find(session => session.kind === "short");
  const data = await realpath(identity.data_dir);
  assert.equal(dirname(dirname(data)).toLowerCase(), (await realpath(tmpdir())).toLowerCase());
  assert(basename(dirname(data)).startsWith("agent-friend-172-"));
  assert.equal(await page.evaluate(async () => (await (await fetch("/v1/companionship")).json()).enabled), false);
  const name = basename(source);
  assert.match(name, /^[\da-f-]{36}\.jsonl$/i);
  const id = name.slice(0, -6);
  const target = join(data, "sessions", name);
  assert.notEqual((await realpath(source)).toLowerCase(), resolve(target).toLowerCase());
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost", "tauri.localhost"].includes(url.hostname)
      ? route.continue() : route.abort();
  });
  const journalHash = await digest(source);
  await copyFile(source, target, constants.COPYFILE_EXCL);
  assert.equal(await digest(target), journalHash);
  const samples = [];
  for (let i = 0; i < 4; i++) {
    // Reload with the short chat selected so the previous JS store cannot
    // answer the open. Listing can initialize sidebar metadata before the click.
    await page.reload({waitUntil:"domcontentloaded"});
    await ready(short);
    const title = await page.evaluate(async sid => {
      const sessions = await (await import("/src/services/api/sessions.ts")).sessionsApi.list();
      return sessions.find(s => s.session_id === sid)?.title || "新对话";
    }, id);
    await page.evaluate(async sid => {
      const store = (await import("/src/stores/conversation.ts")).useConversationStore;
      window.__legacyVisible = null;
      document.addEventListener("pointerdown", () => {
        const start = performance.now();
        function sample() {
          const state = store.getState();
          if (state.currentSessionId === sid && !state.historyLoading) {
            const ids = new Set(state.messages.map(message => message.id));
            const view = document.querySelector(".chat-message-viewport")?.getBoundingClientRect();
            const visible = view && [...document.querySelectorAll("[data-message-id]")].some(node => {
              const rect = node.getBoundingClientRect();
              return ids.has(node.dataset.messageId) && node.textContent.trim() && rect.height &&
                rect.bottom > view.top && rect.top < view.bottom;
            });
            if (visible) {
              requestAnimationFrame(() => {window.__legacyVisible = {
                elapsed_ms:performance.now()-start,
                users:state.messages.filter(message => message.role === "user").length,
              };});
              return;
            }
          }
          if (performance.now()-start < 60000) requestAnimationFrame(sample);
        }
        requestAnimationFrame(sample);
      }, {capture:true, once:true});
    }, id);
    await open({title});
    await page.waitForFunction(() => window.__legacyVisible !== null, undefined, {timeout:65000});
    const sample = await page.evaluate(() => window.__legacyVisible);
    assert.equal(sample.users, 10);
    samples.push({mode:i ? "reopen" : "first_open", ...sample, journal_sha256:journalHash,
      journal_bytes:(await stat(target)).size});
    await open(short);
    await ready(short);
  }
  assert.equal(await digest(target), journalHash);
  return samples;
}
