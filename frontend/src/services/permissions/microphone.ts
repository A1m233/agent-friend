export type MicrophonePermissionAttemptResult =
  | { kind: "completed" }
  | { kind: "denied" }
  | { kind: "timeout" }
  | { kind: "cancelled" }
  | { kind: "failed"; errorName: string };

interface MicrophonePermissionAttemptOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
}

export function acquireMicrophoneCapture(
  constraints: MediaStreamConstraints,
): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia(constraints);
}

const DEFAULT_PERMISSION_ATTEMPT_TIMEOUT_MS = 60_000;

function stopStream(stream: MediaStream): void {
  stream.getTracks().forEach((track) => track.stop());
}

/**
 * permissions 域唯一的 Web Media 授权 executor。
 *
 * resolve 只表示系统交互结束；最终权限真相仍由 Tauri manager 重新查询。
 * timeout/cancel 后若 getUserMedia 迟到 resolve，也会立即释放所有 tracks。
 */
export async function executeMicrophonePermissionAttempt(
  options: MicrophonePermissionAttemptOptions = {},
): Promise<MicrophonePermissionAttemptResult> {
  const getUserMedia =
    options.getUserMedia ??
    acquireMicrophoneCapture;
  const timeoutMs = options.timeoutMs ?? DEFAULT_PERMISSION_ATTEMPT_TIMEOUT_MS;
  let settled = false;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let removeAbortListener: (() => void) | undefined;

  const media = getUserMedia({ audio: true }).then((stream) => {
    if (settled) stopStream(stream);
    return stream;
  });
  const timeoutResult = new Promise<"timeout">((resolve) => {
    timeout = setTimeout(() => resolve("timeout"), timeoutMs);
  });
  const cancelledResult = new Promise<"cancelled">((resolve) => {
    if (!options.signal) return;
    const onAbort = () => resolve("cancelled");
    options.signal.addEventListener("abort", onAbort, { once: true });
    removeAbortListener = () => options.signal?.removeEventListener("abort", onAbort);
    if (options.signal.aborted) onAbort();
  });

  try {
    const result = await Promise.race([
      media.then((stream) => ({ kind: "stream" as const, stream })),
      timeoutResult.then((kind) => ({ kind })),
      cancelledResult.then((kind) => ({ kind })),
    ]);
    settled = true;
    if (result.kind === "stream") {
      stopStream(result.stream);
      return { kind: "completed" };
    }
    return { kind: result.kind };
  } catch (error) {
    settled = true;
    const errorName = error instanceof DOMException ? error.name : "UnknownError";
    if (errorName === "NotAllowedError" || errorName === "SecurityError") {
      return { kind: "denied" };
    }
    return { kind: "failed", errorName };
  } finally {
    if (timeout) clearTimeout(timeout);
    removeAbortListener?.();
  }
}
