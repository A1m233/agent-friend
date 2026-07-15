import { describe, expect, it } from "vitest";

import { locateSettings } from "./navigation";

describe("locateSettings", () => {
  it("resolves every stable settings target", () => {
    for (const target of [
      "general",
      "pet",
      "voice",
      "runtime",
      "archived",
      "feedback",
    ] as const) {
      expect(locateSettings({ target }).section).toBe(target);
    }
  });

  it("keeps feedback preselection only for the feedback target", () => {
    expect(
      locateSettings({
        target: "feedback",
        feedback: { preselectSessionId: "session-1" },
      }),
    ).toEqual({ section: "feedback", preselectSessionId: "session-1" });
    expect(
      locateSettings({
        target: "general",
        feedback: { preselectSessionId: "session-1" },
      }),
    ).toEqual({ section: "general", preselectSessionId: undefined });
  });

  it("falls back safely for missing or unknown targets", () => {
    expect(locateSettings(undefined).section).toBe("general");
    expect(
      locateSettings({ target: "removed" as never }).section,
    ).toBe("general");
  });
});
