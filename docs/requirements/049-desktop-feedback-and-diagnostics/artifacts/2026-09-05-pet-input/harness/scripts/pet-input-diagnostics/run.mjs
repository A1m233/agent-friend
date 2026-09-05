#!/usr/bin/env node
// Read-only dev Pet WebView evidence. Never changes metrics or invokes a setter.
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const TIMEOUT_MS = 10_000;
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function help() {
  console.log(`Read-only dev pet input diagnostics (Node 22+).
Usage: scripts/pet-input-diagnostics/run.* [--cdp URL] [--output PATH]

  --cdp URL      Loopback CDP endpoint (default: http://127.0.0.1:9222)
  --output PATH  New JSON file (default: .tmp/pet-input-diagnostics-<UTC>.json)
  --help         Show this help without connecting

Start dev with VITE_PET_DPR_TRACE=1 to retain its first two seconds of DPR trace.
Only a unique loopback :1420/pet.html Tauri target is accepted; installed builds
are refused. No reload, viewport/metrics change, setter, or application shutdown.
Exit: 0 = captured; 1 = failed; 2 = captured without a stable cursor sample.`);
}

function parseArgs(argv) {
  const options = { cdp: "http://127.0.0.1:9222", output: undefined, help: false };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg === "--cdp" || arg === "--output") {
      const value = argv[++index];
      if (!value || value.startsWith("--")) throw new Error(`Missing value for ${arg}`);
      options[arg.slice(2)] = value;
    } else {
      throw new Error("Unknown argument; use --help for supported options");
    }
  }
  return options;
}

function localUrl(value, protocols) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Invalid endpoint URL");
  }
  if (!protocols.includes(url.protocol) || !LOOPBACK_HOSTS.has(url.hostname)
    || url.username || url.password || url.search || url.hash) {
    throw new Error("Endpoint must be loopback with no credentials, query, or fragment");
  }
  return url;
}

function isDevPetTarget(target) {
  try {
    const url = new URL(target.url);
    return target.type === "page" && ["http:", "https:"].includes(url.protocol)
      && LOOPBACK_HOSTS.has(url.hostname) && url.port === "1420"
      && url.pathname === "/pet.html" && !url.username && !url.password;
  } catch {
    return false;
  }
}

async function connectCdp(url) {
  const socket = new WebSocket(url);
  const pending = new Map();
  let nextId = 0;
  const rejectPending = (message) => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error(message));
    }
    pending.clear();
  };
  socket.addEventListener("message", (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      rejectPending("Invalid CDP response");
      return;
    }
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) {
      request.reject(new Error(`CDP ${request.method} failed (${message.error.code})`));
    } else {
      request.resolve(message.result);
    }
  });
  socket.addEventListener("close", () => rejectPending("CDP connection closed"));
  socket.addEventListener("error", () => rejectPending("CDP connection failed"));
  try {
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => {
        cleanup();
        rejectOpen(new Error("CDP connection timed out after 10 seconds"));
      }, TIMEOUT_MS);
      const onOpen = () => { cleanup(); resolveOpen(); };
      const onError = () => { cleanup(); rejectOpen(new Error("CDP connection failed")); };
      const cleanup = () => {
        clearTimeout(timer);
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("error", onError);
        socket.removeEventListener("close", onError);
      };
      socket.addEventListener("open", onOpen);
      socket.addEventListener("error", onError);
      socket.addEventListener("close", onError);
    });
  } catch (error) {
    socket.close();
    throw error;
  }
  return {
    send(method, params = {}) {
      return new Promise((resolveResult, rejectResult) => {
        const id = ++nextId;
        const timer = setTimeout(() => {
          pending.delete(id);
          rejectResult(new Error(`CDP ${method} timed out after 10 seconds`));
        }, TIMEOUT_MS);
        pending.set(id, { method, resolve: resolveResult, reject: rejectResult, timer });
        try {
          socket.send(JSON.stringify({ id, method, params }));
        } catch {
          clearTimeout(timer);
          pending.delete(id);
          rejectResult(new Error(`Unable to send CDP ${method}`));
        }
      });
    },
    close() {
      rejectPending("CDP collector disconnected");
      socket.close();
    },
  };
}

// Serialized into the existing dev target. Reads only diagnostic state and native
// coordinates; the temporary event subscription is always removed in finally.
async function collectFromPet() {
  const locationUrl = new URL(location.href);
  if (!["http:", "https:"].includes(locationUrl.protocol)
    || !["localhost", "127.0.0.1", "[::1]"].includes(locationUrl.hostname)
    || locationUrl.port !== "1420" || locationUrl.pathname !== "/pet.html") {
    throw new Error("PET_INPUT_NOT_DEV_TARGET");
  }
  const api = window.__TAURI_INTERNALS__;
  const eventApi = window.__TAURI_EVENT_PLUGIN_INTERNALS__;
  if (!api?.invoke || !api.transformCallback || !api.unregisterCallback
    || !eventApi?.unregisterListener || api.metadata?.currentWindow?.label !== "pet") {
    throw new Error("PET_INPUT_TAURI_NOT_READY");
  }
  const deadline = performance.now() + 6_000;
  const bounded = (promise, milliseconds, code) => new Promise((resolveResult, rejectResult) => {
    const timer = setTimeout(() => rejectResult(new Error(code)), milliseconds);
    promise.then(
      (value) => { clearTimeout(timer); resolveResult(value); },
      () => { clearTimeout(timer); rejectResult(new Error(code)); },
    );
  });
  const invokeRead = (command, args = {}) => {
    const remaining = Math.min(700, deadline - performance.now());
    if (remaining <= 0) throw new Error("PET_INPUT_SAMPLING_DEADLINE");
    return bounded(api.invoke(command, args), remaining, "PET_INPUT_NATIVE_READ_FAILED");
  };
  const finitePoint = (point) => point && Number.isFinite(point.x) && Number.isFinite(point.y);
  const samePoint = (left, right) => left.x === right.x && left.y === right.y;
  const readNative = async () => {
    const position = await invokeRead("plugin:window|outer_position", { label: "pet" });
    const cursor = await invokeRead("plugin:window|cursor_position");
    if (!finitePoint(position) || !finitePoint(cursor)) throw new Error("PET_INPUT_INVALID_COORDINATES");
    return { position: { x: position.x, y: position.y }, cursor: { x: cursor.x, y: cursor.y } };
  };
  let eventCount = 0;
  let latest = null;
  let eventId;
  const handler = api.transformCallback((event) => {
    if (finitePoint(event.payload)) {
      eventCount++;
      latest = { x: event.payload.x, y: event.payload.y };
    }
  });
  const subscription = api.invoke("plugin:event|listen", {
    event: "pet://cursor", target: { kind: "AnyLabel", label: "pet" }, handler,
  });
  const attempts = [];
  let stableSample = null;
  const cleanupSubscription = async (id) => {
    try {
      eventApi.unregisterListener("pet://cursor", id);
    } finally {
      await bounded(api.invoke("plugin:event|unlisten", {
        event: "pet://cursor", eventId: id,
      }), 700, "PET_INPUT_UNLISTEN_FAILED");
    }
  };
  try {
    eventId = await bounded(subscription, 700, "PET_INPUT_LISTEN_FAILED");
    for (let index = 0; index < 8; index++) {
      const before = await readNative();
      const beforeDpr = window.devicePixelRatio;
      const countBefore = eventCount;
      await new Promise((resolveWait) => setTimeout(resolveWait, 80));
      const feed = eventCount > countBefore ? latest : null;
      const bracketEventCount = eventCount - countBefore;
      const after = await readNative();
      const afterDpr = window.devicePixelRatio;
      const attempt = {
        index: index + 1, before, after, beforeDpr, afterDpr,
        feed, eventCount: bracketEventCount, inferredFeedDpr: null, status: "no-cursor-event",
      };
      if (feed && samePoint(before.cursor, after.cursor)
        && samePoint(before.position, after.position) && beforeDpr === afterDpr) {
        const ratios = ["x", "y"].flatMap((axis) => {
          const offset = after.cursor[axis] - after.position[axis];
          return Math.abs(feed[axis]) > 0.001 && Math.abs(offset) > 0.001
            ? [offset / feed[axis]] : [];
        });
        if (ratios.length && ratios.every((ratio) => Number.isFinite(ratio) && ratio > 0
          && Math.abs(ratio - ratios[0]) < 0.001)) {
          attempt.inferredFeedDpr = ratios.reduce((sum, ratio) => sum + ratio, 0) / ratios.length;
          attempt.status = "stable";
          stableSample = attempt;
        } else {
          attempt.status = "unusable-coordinate-ratio";
        }
      } else if (feed) {
        attempt.status = "cursor-window-or-dpr-changed";
      }
      attempts.push(attempt);
      if (stableSample) break;
    }
  } finally {
    try {
      if (eventId !== undefined) {
        await cleanupSubscription(eventId);
      } else {
        // A timed-out registration can finish later: remove it rather than leak.
        void subscription.then(cleanupSubscription).catch(() => {});
      }
    } finally {
      api.unregisterCallback(handler);
    }
  }
  const sourceTrace = window.__AGENT_FRIEND_PET_DPR_TRACE__;
  const traceAvailable = sourceTrace?.version === 1 && Array.isArray(sourceTrace.entries);
  // The trace producer currently records only these three primitive details.
  // Do not collect arbitrary page objects, tokens, headers, or conversation data.
  const trace = traceAvailable ? {
    version: 1,
    startedAt: sourceTrace.startedAt,
    entries: sourceTrace.entries.slice(0, 100).map((entry) => ({
      seq: entry.seq, timestampMs: entry.timestampMs, elapsedMs: entry.elapsedMs,
      phase: entry.phase, actualDpr: entry.actualDpr, width: entry.width, height: entry.height,
      details: Object.fromEntries(["dpr", "reason", "reportSeq"]
        .filter((key) => Object.hasOwn(entry.details ?? {}, key))
        .map((key) => [key, entry.details[key]])),
    })),
  } : null;
  const canvas = document.querySelector("canvas");
  return {
    targetUrl: `${locationUrl.origin}${locationUrl.pathname}`,
    traceAvailable, trace,
    devicePixelRatio: window.devicePixelRatio,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    canvas: canvas ? { width: canvas.width, height: canvas.height } : null,
    samplingStatus: stableSample ? "stable" : "no-stable-sample",
    inferredFeedDpr: stableSample?.inferredFeedDpr ?? null,
    stableSample, attempts,
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) { help(); return; }
  if (typeof WebSocket !== "function" || typeof fetch !== "function") {
    throw new Error("Node 22+ with built-in WebSocket and fetch is required");
  }
  const endpoint = localUrl(options.cdp, ["http:", "https:"]);
  if (endpoint.pathname !== "/") throw new Error("CDP endpoint must be an origin without a path");
  const readGit = (...args) => execFileSync("git", args, {
    cwd: ROOT, encoding: "utf8", timeout: TIMEOUT_MS, stdio: ["ignore", "pipe", "pipe"],
  }).trim();
  const sourceCheckout = {
    branch: readGit("rev-parse", "--abbrev-ref", "HEAD"),
    head: readGit("rev-parse", "HEAD"),
  };
  const response = await fetch(new URL("/json/list", endpoint), {
    signal: AbortSignal.timeout(TIMEOUT_MS), redirect: "error",
  });
  if (!response.ok) throw new Error(`CDP target listing failed (HTTP ${response.status})`);
  const targets = await response.json();
  if (!Array.isArray(targets)) throw new Error("Invalid CDP target listing");
  const matching = targets.filter(isDevPetTarget);
  if (matching.length !== 1) {
    throw new Error(`Expected one dev :1420/pet.html target; found ${matching.length}. Installed builds are refused.`);
  }
  const socketUrl = localUrl(matching[0].webSocketDebuggerUrl, ["ws:", "wss:"]);
  if (socketUrl.port !== endpoint.port) throw new Error("CDP WebSocket port differs from the requested endpoint");
  const connection = await connectCdp(socketUrl);
  let evidence;
  try {
    const result = await connection.send("Runtime.evaluate", {
      expression: `(${collectFromPet.toString()})()`, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) {
      const code = result.exceptionDetails.exception?.description?.match(/PET_INPUT_[A-Z_]+/)?.[0];
      throw new Error(code ?? "Pet input evaluation failed; no evidence was saved");
    }
    if (!result.result?.value || result.result.value.targetUrl === undefined) {
      throw new Error("CDP returned no pet input evidence");
    }
    evidence = result.result.value;
  } finally {
    connection.close();
  }
  const capturedAtUtc = new Date().toISOString();
  const output = resolve(ROOT, options.output
    ?? `.tmp/pet-input-diagnostics-${capturedAtUtc.replaceAll(/[:.]/g, "-")}.json`);
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, `${JSON.stringify({
    schemaVersion: 1, capturedAtUtc, sourceCheckout,
    runtimeIdentityVerified: false,
    evidenceScope: "Read-only dev WebView DPR trace and stationary native cursor bracket; checkout metadata alone does not bind the running build.",
    ...evidence,
  }, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
  console.log(`Saved: ${output}`);
  console.log(`Trace: ${evidence.traceAvailable ? evidence.trace.entries.length : "unavailable"}; page DPR: ${evidence.devicePixelRatio}; feed DPR: ${evidence.inferredFeedDpr ?? "unavailable"}`);
  if (evidence.samplingStatus !== "stable") process.exitCode = 2;
}

main().catch((error) => {
  // Errors from page evaluation are reduced to bounded, predefined codes above.
  console.error(`Pet input diagnostics failed: ${error.message}`);
  process.exitCode = 1;
});
