import type { VoiceInputPhase } from "@/services/voiceInput/types";

export function canStartVoiceInput(phase: VoiceInputPhase): boolean {
  return phase === "idle" || phase === "error";
}

export function isVoiceInputLive(phase: VoiceInputPhase): boolean {
  return (
    phase === "requesting_microphone" ||
    phase === "connecting" ||
    phase === "recording" ||
    phase === "stopping"
  );
}

export function canStopVoiceInput(phase: VoiceInputPhase): boolean {
  return phase === "requesting_microphone" || phase === "connecting" || phase === "recording";
}

export function voiceInputActionLabel(phase: VoiceInputPhase): string {
  switch (phase) {
    case "requesting_microphone":
      return "正在请求麦克风";
    case "connecting":
      return "正在连接语音输入";
    case "recording":
      return "停止录音";
    case "stopping":
      return "正在停止录音";
    case "error":
    case "idle":
    default:
      return "语音输入";
  }
}

export function voiceInputStatusLabel(phase: VoiceInputPhase, volumeLevel: number): string {
  if (phase === "requesting_microphone") return "正在请求麦克风";
  if (phase === "connecting") return "正在连接识别服务";
  if (phase === "stopping") return "正在停止";
  if (phase === "recording") {
    if (volumeLevel >= 40) return "音量清晰";
    if (volumeLevel > 3) return "正在收音";
    return "等待声音";
  }
  return "语音输入";
}
