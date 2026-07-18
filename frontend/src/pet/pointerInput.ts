interface PointerButtonLikeEvent {
  button?: unknown;
  isPrimary?: unknown;
}

/**
 * 仅允许主指针的主按键启动或结束桌宠交互。
 *
 * 鼠标左键、触摸主触点和触控笔笔尖的 `button` 都是 0；鼠标右键、
 * 中键和触控笔侧键会被排除。PIXI v7/v8 的 FederatedPointerEvent 均保留
 * PointerEvent 的这两个字段，因此这里不依赖具体 PIXI 版本。
 */
export function isPrimaryPointerButton(event: unknown): boolean {
  if (typeof event !== "object" || event === null) return false;

  const pointerEvent = event as PointerButtonLikeEvent;
  return pointerEvent.button === 0 && pointerEvent.isPrimary !== false;
}
