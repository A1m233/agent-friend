/** Text display evidence only; never an assertion that a person read it. */
export const DISPLAY_DWELL_MS = 2_000;
interface Entry {
  messageId: string;
  version: Promise<string>;
  since: number | null;
  geometry: string;
}
export interface MessageDisplayObservation {
  messageId: string;
  contentVersion: string;
  extent: "partial" | "full";
  dwellMs: number;
}
const entries = new Map<HTMLElement, Entry>();
let lastSampleAt: number | null = null;

export function registerMessageExposure(element: HTMLElement, messageId: string, text: string): () => void {
  const version = crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)).then(
    (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join(""),
  );
  const entry: Entry = { messageId, version, since: null, geometry: "" };
  entries.set(element, entry);
  return () => { if (entries.get(element) === entry) entries.delete(element); };
}

export function resetMessageExposure(): void {
  lastSampleAt = null;
  for (const entry of entries.values()) { entry.since = null; entry.geometry = ""; }
}

export function displayExtent(rect: DOMRectReadOnly, clip: { top: number; left: number; bottom: number; right: number }): "partial" | "full" | null {
  if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= clip.top || rect.top >= clip.bottom
    || rect.right <= clip.left || rect.left >= clip.right) return null;
  return rect.top >= clip.top && rect.bottom <= clip.bottom && rect.left >= clip.left && rect.right <= clip.right ? "full" : "partial";
}

export async function collectMessageExposure(active: boolean, now = performance.now()): Promise<MessageDisplayObservation[]> {
  if (!active || document.visibilityState !== "visible" || !document.hasFocus()) {
    resetMessageExposure(); return [];
  }
  if (lastSampleAt !== null && now - lastSampleAt > 10_000) resetMessageExposure();
  lastSampleAt = now;
  const pending: Promise<MessageDisplayObservation | null>[] = [];
  for (const [wrapper, entry] of entries) {
    const element = wrapper.firstElementChild;
    if (!(element instanceof HTMLElement) || !element.isConnected || !element.getClientRects().length) {
      entry.since = null; continue;
    }
    const rect = element.getBoundingClientRect();
    const clip = { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth };
    let hidden = false;
    for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (style.visibility !== "visible" || style.display === "none" || Number(style.opacity) === 0) { hidden = true; break; }
      const bounds = ancestor.getBoundingClientRect();
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) { clip.top = Math.max(clip.top, bounds.top); clip.bottom = Math.min(clip.bottom, bounds.bottom); }
      if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) { clip.left = Math.max(clip.left, bounds.left); clip.right = Math.min(clip.right, bounds.right); }
    }
    const extent = hidden ? null : displayExtent(rect, clip);
    if (!extent) { entry.since = null; continue; }
    const x = (Math.max(rect.left, clip.left) + Math.min(rect.right, clip.right)) / 2;
    const y = (Math.max(rect.top, clip.top) + Math.min(rect.bottom, clip.bottom)) / 2;
    const top = document.elementFromPoint(x, y);
    if (!top || !(element === top || element.contains(top))) { entry.since = null; continue; }
    const geometry = [rect.top, rect.left, rect.width, rect.height, extent].join(":");
    if (entry.since === null || entry.geometry !== geometry) { entry.since = now; entry.geometry = geometry; continue; }
    const dwellMs = Math.min(60_000, Math.floor(now - entry.since));
    if (dwellMs < DISPLAY_DWELL_MS) continue;
    pending.push(entry.version.then((contentVersion) => entries.get(wrapper) === entry
      ? { messageId: entry.messageId, contentVersion, extent, dwellMs } : null).catch(() => null));
    if (pending.length === 100) break;
  }
  return (await Promise.all(pending)).filter((value): value is MessageDisplayObservation => value !== null);
}
