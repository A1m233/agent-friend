export type VoiceCallPhase =
  | "idle"
  | "confirming_tunnel"
  | "dialing"
  | "joining_room"
  | "starting_agent"
  | "active"
  | "stopping"
  | "ended"
  | "error";

export interface StartVoiceCallRequest {
  sessionId?: string;
  persona?: string;
  model?: string;
  deferStart?: boolean;
}

export interface StartVoiceCallResponse {
  callId: string;
  sessionId: string;
  state: string;
  rtcAppId: string;
  roomId: string;
  userId: string;
  token: string;
}

export interface VoiceCallSnapshot {
  phase: VoiceCallPhase;
  callId: string | null;
  sessionId: string | null;
  startedAt: number | null;
  durationMs: number;
  volumeLevel: number;
  error: string | null;
  consentAccepted: boolean;
}

export interface RtcJoinCredentials {
  rtcAppId: string;
  roomId: string;
  userId: string;
  token: string;
}
