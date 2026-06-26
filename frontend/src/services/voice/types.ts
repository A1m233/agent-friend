export type VoiceCallPhase =
  | "idle"
  | "confirming_tunnel"
  | "preflighting"
  | "dialing"
  | "joining_room"
  | "preparing_microphone"
  | "connecting_agent"
  | "starting_agent"
  | "active"
  | "stopping"
  | "ended"
  | "error";

export type VoiceCallFailureStage =
  | "preflight"
  | "start_call"
  | "rtc_join"
  | "rtc_capture"
  | "rtc_publish"
  | "rtc_join_publish"
  | "rtc_mute"
  | "start_agent"
  | "stop_call"
  | "rtc_cleanup";

export interface VoiceCallDiagnostic {
  stage: VoiceCallFailureStage;
  message: string;
}

export interface StartVoiceCallRequest {
  sessionId?: string;
  persona?: string;
  model?: string;
  deferStart?: boolean;
  traceId?: string;
}

export interface StartVoiceCallResponse {
  callId: string;
  sessionId: string;
  state: string;
  rtcAppId: string;
  roomId: string;
  userId: string;
  token: string;
  traceId: string;
}

export interface VoiceCallSnapshot {
  phase: VoiceCallPhase;
  callId: string | null;
  sessionId: string | null;
  startedAt: number | null;
  durationMs: number;
  volumeLevel: number;
  muted: boolean;
  error: string | null;
  diagnostic: VoiceCallDiagnostic | null;
  traceId: string | null;
}

export interface RtcJoinCredentials {
  rtcAppId: string;
  roomId: string;
  userId: string;
  token: string;
}
