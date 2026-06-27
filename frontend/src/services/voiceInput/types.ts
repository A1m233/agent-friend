export type VoiceInputPhase =
  | "idle"
  | "requesting_microphone"
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
  | { type: "partial"; traceId: string; text: string; stableText?: string }
  | { type: "final"; traceId: string; text: string }
  | {
      type: "stopped";
      traceId: string;
      reason: "client_stop" | "client_cancel" | "provider_done";
    }
  | { type: "error"; traceId: string; code: string; message: string };

export interface VoiceInputTranscript {
  kind: "partial" | "final";
  traceId: string;
  text: string;
  seq: number;
}

export interface VoiceInputSnapshot {
  phase: VoiceInputPhase;
  traceId: string | null;
  volumeLevel: number;
  latestTranscript: VoiceInputTranscript | null;
  error: string | null;
  errorCode: string | null;
  startedAt: number | null;
  lastEventAt: number | null;
}
