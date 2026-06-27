import type {
  ServerVoiceInputEvent,
  VoiceInputAudioOptions,
} from "@/services/voiceInput/types";

interface ConnectVoiceInputOptions {
  traceId: string;
  audio: VoiceInputAudioOptions;
  locale?: string;
  onEvent: (event: ServerVoiceInputEvent) => void;
  onClose?: () => void;
  onError?: (error: unknown) => void;
}

export interface VoiceInputConnection {
  sendAudio: (chunk: ArrayBuffer) => void;
  stop: () => void;
  cancel: () => void;
  close: () => void;
}

interface PrewarmVoiceInputOptions {
  traceId: string;
  reason: string;
  signal?: AbortSignal;
}

export interface VoiceInputPrewarmResult {
  status: "started" | "already_warm" | "disabled" | "unavailable" | "error";
  traceId: string;
  ttlMs: number;
  warmAgeMs?: number;
  message?: string;
}

const VOICE_INPUT_CONNECT_TIMEOUT_MS = 12_000;

function voiceInputHttpBase(): string {
  return import.meta.env.DEV ? window.location.origin : "http://127.0.0.1:18900";
}

function voiceInputWsUrl(traceId: string): string {
  const url = new URL("/voice/transcriptions/stream", voiceInputHttpBase());
  url.searchParams.set("trace_id", traceId);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function parseServerEvent(raw: string): ServerVoiceInputEvent | null {
  const parsed = JSON.parse(raw) as Partial<ServerVoiceInputEvent>;
  if (!parsed || typeof parsed.type !== "string") return null;
  return parsed as ServerVoiceInputEvent;
}

export function connectVoiceInput(options: ConnectVoiceInputOptions): Promise<VoiceInputConnection> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(voiceInputWsUrl(options.traceId));
    let opened = false;
    let settled = false;
    const timeoutId = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      ws.close();
      reject(new Error("语音输入服务连接超时"));
    }, VOICE_INPUT_CONNECT_TIMEOUT_MS);

    const clearConnectTimeout = () => {
      window.clearTimeout(timeoutId);
    };

    const sendControl = (type: "stop" | "cancel") => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type }));
    };

    const connection: VoiceInputConnection = {
      sendAudio(chunk) {
        if (ws.readyState !== WebSocket.OPEN) return;
        ws.send(chunk);
      },
      stop() {
        sendControl("stop");
      },
      cancel() {
        sendControl("cancel");
      },
      close() {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
      },
    };

    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      opened = true;
      clearConnectTimeout();
      ws.send(
        JSON.stringify({
          type: "start",
          traceId: options.traceId,
          audio: options.audio,
          locale: options.locale ?? "zh-CN",
        }),
      );
      settled = true;
      resolve(connection);
    };
    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const parsed = parseServerEvent(event.data);
        if (parsed) options.onEvent(parsed);
      } catch (error) {
        options.onError?.(error);
      }
    };
    ws.onerror = (event) => {
      options.onError?.(event);
      if (!settled) {
        clearConnectTimeout();
        settled = true;
        reject(new Error("语音输入服务连接失败"));
      }
    };
    ws.onclose = () => {
      if (opened) options.onClose?.();
      if (!opened && !settled) {
        clearConnectTimeout();
        settled = true;
        reject(new Error("语音输入服务没有响应"));
      }
    };
  });
}

export async function prewarmVoiceInput(
  options: PrewarmVoiceInputOptions,
): Promise<VoiceInputPrewarmResult> {
  const url = new URL("/voice/transcriptions/prewarm", voiceInputHttpBase());
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ traceId: options.traceId, reason: options.reason }),
    signal: options.signal,
  });
  if (!response.ok) {
    throw new Error(`voice input prewarm failed: ${response.status}`);
  }
  return (await response.json()) as VoiceInputPrewarmResult;
}
