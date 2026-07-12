import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const requireFromFrontend = createRequire(resolve(root, "frontend/package.json"));
const { chromium } = requireFromFrontend("playwright");

const value = process.argv[2];
if (!["on", "off", "inspect", "bottom", "state", "messages", "sessions"].includes(value)) {
  throw new Error("usage: control.mjs on|off|inspect|bottom|state|messages|sessions");
}

const browser = await chromium.connectOverCDP("http://127.0.0.1:9222");
try {
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().includes("chat.html"));
  if (!page) throw new Error("chat WebView page not found");
  if (value === "sessions") {
    const state = await page.evaluate(async () => {
      const module = await import("/src/stores/sessions.ts");
      const current = module.useSessionsStore.getState();
      return {
        loaded: current.loaded,
        sessionCount: current.list.length,
        sessions: current.list.slice(0, 3).map((session) => ({
          sessionId: session.session_id,
          title: session.title,
          preview: session.preview,
          updatedAt: session.updated_at,
        })),
      };
    });
    process.stdout.write(JSON.stringify({ status: "ok", ...state }) + "\n");
  } else if (value === "state" || value === "messages") {
    const state = await page.evaluate(async () => {
      const module = await import("/src/stores/conversation.ts");
      const current = module.useConversationStore.getState();
      return {
        currentSessionId: current.currentSessionId,
        streaming: current.streaming,
        historyLoading: current.historyLoading,
        syncRefreshSeq: current.syncRefreshSeq,
        messageCount: current.messages.length,
        messages: current.messages.slice(-4).map((message) => ({
          role: message.role,
          text: message.blocks
            .filter((block) => block.kind === "text")
            .map((block) => block.text)
            .join("\n\n"),
        })),
      };
    });
    if (value === "state") delete state.messages;
    process.stdout.write(JSON.stringify({ status: "ok", ...state }) + "\n");
  } else if (value === "inspect" || value === "bottom") {
    const state = await page.evaluate((action) => {
      const viewport = document.querySelector(".chat-message-viewport");
      if (!(viewport instanceof HTMLElement)) return { found: false };
      if (action === "bottom") {
        viewport.scrollTop = viewport.scrollHeight;
        viewport.dispatchEvent(new Event("scroll", { bubbles: true }));
      }
      return {
        found: true,
        scrollTop: viewport.scrollTop,
        scrollHeight: viewport.scrollHeight,
        clientHeight: viewport.clientHeight,
        nearBottom: viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 16,
      };
    }, value);
    process.stdout.write(JSON.stringify({ status: "ok", ...state }) + "\n");
  } else {
    await page.evaluate(async (streaming) => {
      const module = await import("/src/stores/conversation.ts");
      module.useConversationStore.setState({ streaming });
    }, value === "on");
    process.stdout.write(JSON.stringify({ status: "ok", streaming: value === "on" }) + "\n");
  }
} finally {
  await browser.close();
}
