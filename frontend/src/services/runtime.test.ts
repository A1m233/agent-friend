import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@/utils/tauri", () => ({ isTauri: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import {
  ensureLocalServiceReady,
  resetLocalServiceReadinessForTest,
} from "./runtime";

const mockInvoke = vi.mocked(invoke);
const mockIsTauri = vi.mocked(isTauri);

describe("ensureLocalServiceReady", () => {
  beforeEach(() => {
    resetLocalServiceReadinessForTest();
    mockInvoke.mockReset();
    mockIsTauri.mockReset();
  });

  it("is a no-op in browser development", async () => {
    mockIsTauri.mockReturnValue(false);
    await ensureLocalServiceReady("bridge");
    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("deduplicates concurrent Tauri startup requests", async () => {
    mockIsTauri.mockReturnValue(true);
    mockInvoke.mockResolvedValue(undefined);
    await Promise.all([
      ensureLocalServiceReady("bridge"),
      ensureLocalServiceReady("bridge"),
    ]);
    expect(mockInvoke).toHaveBeenCalledTimes(1);
    expect(mockInvoke).toHaveBeenCalledWith("start_runtime_service", {
      service: "bridge",
    });
  });

  it("allows retry after startup failure", async () => {
    mockIsTauri.mockReturnValue(true);
    mockInvoke
      .mockRejectedValueOnce(new Error("not ready"))
      .mockResolvedValueOnce(undefined);

    await expect(ensureLocalServiceReady("voice")).rejects.toThrow("not ready");
    await expect(ensureLocalServiceReady("voice")).resolves.toBeUndefined();
    expect(mockInvoke).toHaveBeenCalledTimes(2);
  });
});
