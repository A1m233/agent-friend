import type { VoiceCallFailureStage, VoiceCallPhase } from "@/services/voice/types";

export function canStartVoiceCall(phase: VoiceCallPhase): boolean {
  return phase === "idle" || phase === "ended" || phase === "error";
}

export function isVoiceCallBlockingText(phase: VoiceCallPhase): boolean {
  return (
    phase === "dialing" ||
    phase === "preflighting" ||
    phase === "joining_room" ||
    phase === "preparing_microphone" ||
    phase === "connecting_agent" ||
    phase === "starting_agent" ||
    phase === "active" ||
    phase === "stopping"
  );
}

export function isVoiceCallLive(phase: VoiceCallPhase): boolean {
  return (
    phase === "dialing" ||
    phase === "preflighting" ||
    phase === "joining_room" ||
    phase === "preparing_microphone" ||
    phase === "connecting_agent" ||
    phase === "starting_agent" ||
    phase === "active"
  );
}

export function voicePhaseLabel(phase: VoiceCallPhase): string {
  switch (phase) {
    case "confirming_tunnel":
      return "等待确认";
    case "preflighting":
      return "正在检查麦克风...";
    case "dialing":
      return "正在创建通话...";
    case "joining_room":
      return "正在进入房间...";
    case "preparing_microphone":
      return "正在准备麦克风...";
    case "connecting_agent":
      return "正在连接她...";
    case "starting_agent":
      return "正在叫醒她...";
    case "active":
      return "正在听...";
    case "stopping":
      return "正在挂断...";
    case "error":
      return "没有接通";
    case "ended":
      return "已挂断";
    case "idle":
    default:
      return "准备拨号";
  }
}

export function voiceFailureStageLabel(stage: VoiceCallFailureStage): string {
  switch (stage) {
    case "preflight":
      return "检查麦克风";
    case "start_call":
      return "创建通话";
    case "rtc_join":
      return "RTC 入房";
    case "rtc_capture":
      return "麦克风采集";
    case "rtc_publish":
      return "发布麦克风";
    case "rtc_join_publish":
      return "RTC 入房/发布";
    case "rtc_mute":
      return "切换静音";
    case "start_agent":
      return "启动语音 agent";
    case "stop_call":
      return "通知挂断";
    case "rtc_cleanup":
      return "释放 RTC";
  }
}
