export interface ChatSenderEditorState {
  text: string;
  caret: number;
  composing: boolean;
}

// ChatSender has no public selection API. Keep its nested Web Component
// textarea access at this adapter boundary and exercise the real component.
export function findSenderTextarea(root: ParentNode): HTMLTextAreaElement | null {
  const textarea = root.querySelector("textarea");
  if (textarea) return textarea;
  for (const child of root.querySelectorAll("*")) {
    if (child.shadowRoot) {
      const found = findSenderTextarea(child.shadowRoot);
      if (found) return found;
    }
  }
  if (root instanceof HTMLElement && root.shadowRoot) return findSenderTextarea(root.shadowRoot);
  return null;
}
