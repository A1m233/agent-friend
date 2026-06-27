import { error, info, warn } from "@tauri-apps/plugin-log";

const FRAME_RE =
  /\(?(?:[^()]*?\/)?([^/()\s]+\.(?:tsx?|jsx?|mjs|cjs))(?::\d+)?:\d+\)?/;
const TAURI_STALE_CALLBACK_RE =
  /^\[TAURI\] Couldn't find callback id \d+\. This might happen when the app is reloaded while Rust is running an asynchronous operation\.$/;
const TAURI_STALE_CALLBACK_THROTTLE_MS = 5_000;

let lastTauriStaleCallbackForwardedAt = 0;

function pickCaller(): string {
  const stack = new Error().stack ?? "";
  const lines = stack.split("\n").slice(3);
  for (const line of lines) {
    const m = FRAME_RE.exec(line);
    if (m) return m[1];
  }
  return "unknown";
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function shouldForward(level: "info" | "warn" | "error", message: string): boolean {
  if (level !== "warn" || !TAURI_STALE_CALLBACK_RE.test(message)) return true;

  const now = Date.now();
  if (now - lastTauriStaleCallbackForwardedAt < TAURI_STALE_CALLBACK_THROTTLE_MS) {
    return false;
  }
  lastTauriStaleCallbackForwardedAt = now;
  return true;
}

function forward(level: "info" | "warn" | "error", args: unknown[]) {
  const message = args
    .map((a) => (typeof a === "string" ? a : safeStringify(a)))
    .join(" ");
  if (!shouldForward(level, message)) return;

  const file = pickCaller();
  const opts = { file };
  const fn = level === "info" ? info : level === "warn" ? warn : error;
  fn(message, opts).catch(() => {
    // 转发到 Tauri plugin 必须永不抛回业务代码。
  });
}

function patch() {
  const orig = {
    info: console.info.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
  };

  console.info = (...args: unknown[]) => {
    orig.info(...args);
    forward("info", args);
  };
  console.warn = (...args: unknown[]) => {
    orig.warn(...args);
    forward("warn", args);
  };
  console.error = (...args: unknown[]) => {
    orig.error(...args);
    forward("error", args);
  };
}

const SENTINEL = Symbol.for("agent-friend.console-patched");
type PatchedConsole = typeof console & { [SENTINEL]?: boolean };

if (!(console as PatchedConsole)[SENTINEL]) {
  patch();
  (console as PatchedConsole)[SENTINEL] = true;
}
