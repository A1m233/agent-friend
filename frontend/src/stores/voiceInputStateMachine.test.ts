import { describe, expect, it } from "vitest";

import {
  canStartVoiceInput,
  canStopVoiceInput,
  isVoiceInputLive,
  voiceInputActionLabel,
  voiceInputStatusLabel,
} from "./voiceInputStateMachine";

describe("voiceInputStateMachine", () => {
  it("allows start only from idle or error", () => {
    expect(canStartVoiceInput("idle")).toBe(true);
    expect(canStartVoiceInput("error")).toBe(true);
    expect(canStartVoiceInput("recording")).toBe(false);
    expect(canStartVoiceInput("stopping")).toBe(false);
  });

  it("marks mic/connection phases as live", () => {
    expect(isVoiceInputLive("requesting_microphone")).toBe(true);
    expect(isVoiceInputLive("connecting")).toBe(true);
    expect(isVoiceInputLive("recording")).toBe(true);
    expect(isVoiceInputLive("stopping")).toBe(true);
    expect(isVoiceInputLive("idle")).toBe(false);
  });

  it("allows stop before the stop request is already in flight", () => {
    expect(canStopVoiceInput("requesting_microphone")).toBe(true);
    expect(canStopVoiceInput("connecting")).toBe(true);
    expect(canStopVoiceInput("recording")).toBe(true);
    expect(canStopVoiceInput("stopping")).toBe(false);
  });

  it("labels the toggle action and recording status", () => {
    expect(voiceInputActionLabel("idle")).toBe("语音输入");
    expect(voiceInputActionLabel("recording")).toBe("停止录音");
    expect(voiceInputStatusLabel("recording", 3)).toBe("等待声音");
    expect(voiceInputStatusLabel("recording", 10)).toBe("正在收音");
    expect(voiceInputStatusLabel("recording", 80)).toBe("音量清晰");
  });
});
