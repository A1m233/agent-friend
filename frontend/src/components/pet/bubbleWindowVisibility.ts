import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";

export interface BubbleWindowVisibility {
  setVisible(visible: boolean): Promise<void>;
  dispose(): void;
}

// Native commands can outlive a React effect. A replacement owner must wait for
// the previous RPC before applying its own DOM-derived visibility.
let lane: Promise<void> = Promise.resolve();
let activeOwner: object | null = null;
let latestRequest = 0;
let settled: boolean | null = null;

/** The caller includes exiting DOM nodes until they have actually been removed. */
export function acquireBubbleWindowVisibility(): BubbleWindowVisibility {
  if (!isTauri()) return { setVisible: async () => {}, dispose() {} };
  const owner = {};
  activeOwner = owner;
  settled = null; // The first DOM measurement also corrects a native window left by reload.

  return {
    setVisible(visible) {
      if (activeOwner !== owner) return Promise.resolve();
      const request = ++latestRequest;
      const operation = lane.then(async () => {
        if (activeOwner !== owner || request !== latestRequest || settled === visible) return;
        try {
          await invoke(visible ? "show_bubble" : "hide_bubble");
          settled = visible;
        } catch (error) {
          settled = null;
          throw error;
        }
      });
      // A rejection reaches its caller, but cannot poison the next explicit request.
      lane = operation.catch(() => undefined);
      return operation;
    },
    dispose() {
      if (activeOwner === owner) activeOwner = null;
    },
  };
}
