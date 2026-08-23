import { describe, expect, it } from "vitest";
import {
  AVATAR_SCALE_STEP_PERCENT,
  DEFAULT_SETTINGS,
  getAvatarModelScalePercent,
} from "./index";

describe("avatar scale settings", () => {
  it("uses product defaults", () => {
    expect(DEFAULT_SETTINGS.immersiveMode).toBe(true);
    expect(DEFAULT_SETTINGS.reminderNotificationMode).toBe("smart");
    expect(DEFAULT_SETTINGS.displayRecoveryProtectionEnabled).toBe(false);
    expect(DEFAULT_SETTINGS.chatModel).toEqual({
      provider: "kimi",
      model: "kimi/kimi-k2.5",
    });
    expect(DEFAULT_SETTINGS.memoryModel).toEqual({
      provider: "deepseek",
      model: "deepseek/deepseek-v4-flash",
    });
    expect(DEFAULT_SETTINGS.avatarModelScalePercents).toEqual({});
    expect(DEFAULT_SETTINGS.petMiniScalePercent).toBe(50);
    expect(AVATAR_SCALE_STEP_PERCENT).toBe(1);
    expect(getAvatarModelScalePercent({}, "model-a")).toBe(100);
  });

  it("reads per-model values and clamps corrupt snapshots", () => {
    expect(getAvatarModelScalePercent({ a: 135, b: 999, c: 1 }, "a")).toBe(135);
    expect(getAvatarModelScalePercent({ b: 999 }, "b")).toBe(200);
    expect(getAvatarModelScalePercent({ c: 1 }, "c")).toBe(50);
  });
});
