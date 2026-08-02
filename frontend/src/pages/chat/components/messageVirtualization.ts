interface MessageVirtualIdentity {
  id: string;
}

export const MESSAGE_VIRTUALIZER_ESTIMATED_SIZE_PX = 120;
export const MESSAGE_VIRTUALIZER_GAP_PX = 16;
export const MESSAGE_VIRTUALIZER_PADDING_START_PX = 16;
export const MESSAGE_VIRTUALIZER_OVERSCAN = 6;

/**
 * Keep a message's virtual row identity stable when older history is prepended.
 * The index fallback only covers the transient out-of-range reads allowed by the
 * virtualizer while options are being reconciled.
 */
export function getMessageVirtualItemKey(
  messages: readonly MessageVirtualIdentity[],
  index: number,
): string | number {
  return messages[index]?.id ?? index;
}
