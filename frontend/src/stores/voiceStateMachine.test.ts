import { describe, expect, it } from "vitest";

import {
  canStartVoiceCall,
  isVoiceCallBlockingText,
  isVoiceCallLive,
  voiceFailureStageLabel,
  voicePhaseLabel,
} from "./voiceStateMachine";

describe("voiceStateMachine", () => {
  it("只在空闲、结束、错误态允许开始新通话", () => {
    expect(canStartVoiceCall("idle")).toBe(true);
    expect(canStartVoiceCall("ended")).toBe(true);
    expect(canStartVoiceCall("error")).toBe(true);
    expect(canStartVoiceCall("dialing")).toBe(false);
    expect(canStartVoiceCall("preflighting")).toBe(false);
    expect(canStartVoiceCall("preparing_microphone")).toBe(false);
    expect(canStartVoiceCall("connecting_agent")).toBe(false);
    expect(canStartVoiceCall("active")).toBe(false);
    expect(canStartVoiceCall("stopping")).toBe(false);
  });

  it("通话链路占用当前文字输入", () => {
    expect(isVoiceCallBlockingText("dialing")).toBe(true);
    expect(isVoiceCallBlockingText("preflighting")).toBe(true);
    expect(isVoiceCallBlockingText("joining_room")).toBe(true);
    expect(isVoiceCallBlockingText("preparing_microphone")).toBe(true);
    expect(isVoiceCallBlockingText("connecting_agent")).toBe(true);
    expect(isVoiceCallBlockingText("starting_agent")).toBe(true);
    expect(isVoiceCallBlockingText("active")).toBe(true);
    expect(isVoiceCallBlockingText("stopping")).toBe(true);
    expect(isVoiceCallBlockingText("confirming_tunnel")).toBe(false);
    expect(isVoiceCallBlockingText("ended")).toBe(false);
  });

  it("live 态不包含 stopping", () => {
    expect(isVoiceCallLive("active")).toBe(true);
    expect(isVoiceCallLive("stopping")).toBe(false);
  });

  it("提供用户可见状态文案", () => {
    expect(voicePhaseLabel("active")).toBe("正在听...");
    expect(voicePhaseLabel("error")).toBe("没有接通");
  });

  it("提供失败阶段诊断文案", () => {
    expect(voiceFailureStageLabel("preflight")).toBe("检查麦克风");
    expect(voiceFailureStageLabel("start_call")).toBe("创建通话");
    expect(voiceFailureStageLabel("rtc_publish")).toBe("发布麦克风");
    expect(voiceFailureStageLabel("rtc_join_publish")).toBe("RTC 入房/发布");
    expect(voiceFailureStageLabel("start_agent")).toBe("启动语音 agent");
  });
});
