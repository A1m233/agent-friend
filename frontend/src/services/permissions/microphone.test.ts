import { describe, expect, it, vi } from "vitest";
import { executeMicrophonePermissionAttempt } from "./microphone";

function fakeStream() {
  const stop = vi.fn();
  return {
    stream: { getTracks: () => [{ stop }] } as unknown as MediaStream,
    stop,
  };
}

describe("executeMicrophonePermissionAttempt", () => {
  it("releases the temporary stream immediately after the prompt resolves", async () => {
    const { stream, stop } = fakeStream();
    await expect(
      executeMicrophonePermissionAttempt({ getUserMedia: async () => stream }),
    ).resolves.toEqual({ kind: "completed" });
    expect(stop).toHaveBeenCalledOnce();
  });

  it("maps platform denial without opening another device", async () => {
    const denied = new DOMException("denied", "NotAllowedError");
    await expect(
      executeMicrophonePermissionAttempt({
        getUserMedia: async () => Promise.reject(denied),
      }),
    ).resolves.toEqual({ kind: "denied" });
  });

  it("cleans a stream that resolves after timeout", async () => {
    const { stream, stop } = fakeStream();
    let resolveMedia: ((stream: MediaStream) => void) | undefined;
    const media = new Promise<MediaStream>((resolve) => {
      resolveMedia = resolve;
    });
    await expect(
      executeMicrophonePermissionAttempt({
        timeoutMs: 1,
        getUserMedia: async () => media,
      }),
    ).resolves.toEqual({ kind: "timeout" });

    resolveMedia?.(stream);
    await Promise.resolve();
    await Promise.resolve();
    expect(stop).toHaveBeenCalledOnce();
  });
});
