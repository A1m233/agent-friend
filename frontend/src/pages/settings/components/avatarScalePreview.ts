export interface AvatarScalePreviewPayload {
  modelId?: string;
  modelScalePercent?: number;
  miniScalePercent?: number;
}

type RequestFrame = (callback: FrameRequestCallback) => number;
type CancelFrame = (handle: number) => void;

export interface AvatarScalePreviewScheduler {
  queue(payload: AvatarScalePreviewPayload): void;
  flush(): void;
  dispose(): void;
}

/** 同一动画帧内只发布最后合并出的缩放预览，避免跨 WebView 事件积压。 */
export function createAvatarScalePreviewScheduler(
  publish: (payload: AvatarScalePreviewPayload) => void,
  requestFrame: RequestFrame = window.requestAnimationFrame.bind(window),
  cancelFrame: CancelFrame = window.cancelAnimationFrame.bind(window),
): AvatarScalePreviewScheduler {
  let pending: AvatarScalePreviewPayload | null = null;
  let frameHandle: number | null = null;

  const flush = () => {
    if (frameHandle !== null) {
      cancelFrame(frameHandle);
      frameHandle = null;
    }
    if (!pending) return;
    const payload = pending;
    pending = null;
    publish(payload);
  };

  return {
    queue(payload) {
      pending = { ...pending, ...payload };
      if (frameHandle !== null) return;
      frameHandle = requestFrame(() => {
        frameHandle = null;
        if (!pending) return;
        const next = pending;
        pending = null;
        publish(next);
      });
    },
    flush,
    dispose() {
      if (frameHandle !== null) cancelFrame(frameHandle);
      frameHandle = null;
      pending = null;
    },
  };
}
