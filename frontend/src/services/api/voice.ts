import { createHttp, type FriendlyError } from "@/services/http";
import type {
  StartVoiceCallRequest,
  StartVoiceCallResponse,
  VoiceCallBackendEvent,
} from "@/services/voice/types";

interface RawStartVoiceCallResponse {
  call_id: string;
  session_id: string;
  state: string;
  rtc_app_id: string;
  room_id: string;
  user_id: string;
  token: string;
  trace_id?: string;
}

interface RawStopVoiceCallResponse {
  call_id: string;
  state: string;
}

function voiceBaseUrl(): string {
  return import.meta.env.DEV ? "" : "http://127.0.0.1:18900";
}

function voiceEventsBaseUrl(): string {
  return import.meta.env.DEV ? window.location.origin : "http://127.0.0.1:18900";
}

function voiceEventsUrl(callId: string): string {
  const url = new URL(`/voice/calls/${callId}/events`, voiceEventsBaseUrl());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

const voiceHttp = createHttp(voiceBaseUrl());

function toStartResponse(raw: RawStartVoiceCallResponse): StartVoiceCallResponse {
  return {
    callId: raw.call_id,
    sessionId: raw.session_id,
    state: raw.state,
    rtcAppId: raw.rtc_app_id,
    roomId: raw.room_id,
    userId: raw.user_id,
    token: raw.token,
    traceId: raw.trace_id ?? raw.call_id,
  };
}

function bodyOf(req: StartVoiceCallRequest): Record<string, unknown> {
  return {
    session_id: req.sessionId,
    persona: req.persona,
    model: req.model,
    defer_start: req.deferStart ?? true,
    trace_id: req.traceId,
  };
}

export function voiceErrorMessage(error: unknown): string {
  const friendly = error as Partial<FriendlyError>;
  if (typeof friendly.friendly === "string" && friendly.friendly) return friendly.friendly;
  return "语音通话暂时接不上，检查一下 voice_bridge 和公网穿透后再试试？";
}

export const voiceApi = {
  async startCall(req: StartVoiceCallRequest): Promise<StartVoiceCallResponse> {
    const raw = await voiceHttp.post<unknown, RawStartVoiceCallResponse>(
      "/voice/calls",
      bodyOf(req),
    );
    return toStartResponse(raw);
  },

  async startAgent(callId: string): Promise<void> {
    await voiceHttp.post(`/voice/calls/${callId}/start-agent`);
  },

  async stopCall(callId: string): Promise<string> {
    const raw = await voiceHttp.post<unknown, RawStopVoiceCallResponse>(
      `/voice/calls/${callId}/stop`,
    );
    return raw.state;
  },

  subscribeCallEvents(
    callId: string,
    onEvent: (event: VoiceCallBackendEvent) => void,
    onError?: (error: unknown) => void,
  ): () => void {
    const ws = new WebSocket(voiceEventsUrl(callId));
    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const parsed = JSON.parse(event.data) as VoiceCallBackendEvent;
        if (parsed.type) onEvent(parsed);
      } catch (error) {
        onError?.(error);
      }
    };
    ws.onerror = (event) => onError?.(event);
    return () => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    };
  },
};
