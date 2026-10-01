export type VoiceInputPhase =
  | "idle"
  | "waiting_permission_flow"
  | "connecting"
  | "recording"
  | "stopping"
  | "error";

export interface VoiceInputAudioOptions {
  format: "pcm16" | "webm-opus";
  sampleRate: number;
  channels: 1 | 2;
}

export type ServerVoiceInputEvent =
  | { type: "ready"; traceId: string }
  | { type: "partial"; traceId: string; text: string; stableText?: string; spans?: VoiceTranscriptSpan[] }
  | { type: "final"; traceId: string; text: string; spans?: VoiceTranscriptSpan[] }
  | {
      type: "stopped";
      traceId: string;
      reason: "client_stop" | "client_cancel" | "provider_done";
    }
  | { type: "error"; traceId: string; code: string; message: string };

export interface VoiceTranscriptSpan {
  text: string;
  startMs: number;
  endMs: number;
}

export interface VoiceInputTranscript {
  kind: "partial" | "final";
  traceId: string;
  targetWorkspaceKey: string;
  text: string;
  seq: number;
  spans?: VoiceTranscriptSpan[];
}

export interface VoiceInputSnapshot {
  phase: VoiceInputPhase;
  traceId: string | null;
  targetWorkspaceKey: string | null;
  volumeLevel: number;
  latestTranscript: VoiceInputTranscript | null;
  error: string | null;
  errorCode: string | null;
  startedAt: number | null;
  lastEventAt: number | null;
}
