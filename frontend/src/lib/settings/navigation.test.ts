import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@/utils/tauri", () => ({ isTauri: vi.fn(() => true) }));

import { invoke } from "@tauri-apps/api/core";
import {
  DEFAULT_SETTINGS_TARGET,
  locateSettings,
  openFeedback,
  openSettings,
  PRIMARY_SETTINGS_SECTIONS,
} from "./navigation";

const mockInvoke = vi.mocked(invoke);

describe("locateSettings", () => {
  it("resolves every stable settings target", () => {
    for (const target of [
      ...PRIMARY_SETTINGS_SECTIONS.map(({ key }) => key),
      "feedback" as const,
    ]) {
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
    expect(DEFAULT_SETTINGS_TARGET).toBe(PRIMARY_SETTINGS_SECTIONS[0].key);
    expect(locateSettings(undefined).section).toBe(PRIMARY_SETTINGS_SECTIONS[0].key);
    expect(
      locateSettings({ target: "removed" as never }).section,
    ).toBe(PRIMARY_SETTINGS_SECTIONS[0].key);
  });
});

describe("openFeedback", () => {
  beforeEach(() => mockInvoke.mockReset());

  it("opens the shared feedback destination without context", async () => {
    mockInvoke.mockResolvedValue(undefined);

    await openFeedback();

    expect(mockInvoke).toHaveBeenCalledWith("open_settings", {
      request: { target: "feedback", feedback: undefined },
    });
  });

  it("forwards the source session for feedback preselection", async () => {
    mockInvoke.mockResolvedValue(undefined);

    await openFeedback({ preselectSessionId: "session-1" });

    expect(mockInvoke).toHaveBeenCalledWith("open_settings", {
      request: {
        target: "feedback",
        feedback: { preselectSessionId: "session-1" },
      },
    });
  });
});

describe("openSettings", () => {
  beforeEach(() => mockInvoke.mockReset());

  it("forwards the desktop pet target through the shared navigation command", async () => {
    mockInvoke.mockResolvedValue(undefined);

    await openSettings({ target: "pet" });

    expect(mockInvoke).toHaveBeenCalledWith("open_settings", {
      request: { target: "pet" },
    });
  });
});
