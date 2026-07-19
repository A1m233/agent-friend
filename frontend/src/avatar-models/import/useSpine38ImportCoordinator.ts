import { useEffect } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { UserModelScanSnapshot, UserSpineCandidate } from "../installed";
import { isTauri } from "@/utils/tauri";

interface WorkerResponse {
  requestId: string;
  success: boolean;
  defaultAnimation?: string;
  error?: string;
}

/** Only the pet WebView mounts this hook, making runtime validation a single-writer flow. */
export function useSpine38ImportCoordinator(): void {
  useEffect(() => {
    if (!isTauri()) return;
    const worker = new Worker(new URL("./spine38Import.worker.ts", import.meta.url), {
      type: "module",
    });
    const queue: UserSpineCandidate[] = [];
    const handled = new Set<string>();
    let active: UserSpineCandidate | null = null;
    let cancelled = false;

    const runNext = () => {
      if (cancelled || active || queue.length === 0) return;
      active = queue.shift() ?? null;
      if (!active) return;
      worker.postMessage({
        requestId: `${active.id}@${active.contentRevision}`,
        skeletonUrl: convertFileSrc(active.skeletonPath),
        atlasUrl: convertFileSrc(active.atlasPath),
        textureUrls: active.texturePaths.map((path) => convertFileSrc(path)),
      });
    };

    const enqueue = (snapshot: UserModelScanSnapshot) => {
      for (const candidate of snapshot.candidates) {
        const key = `${candidate.id}@${candidate.contentRevision}`;
        if (handled.has(key)) continue;
        handled.add(key);
        queue.push(candidate);
      }
      runNext();
    };

    worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
      const candidate = active;
      active = null;
      if (!candidate || event.data.requestId !== `${candidate.id}@${candidate.contentRevision}`) {
        runNext();
        return;
      }
      void invoke("complete_user_avatar_model_validation", {
        result: {
          id: candidate.id,
          contentRevision: candidate.contentRevision,
          success: event.data.success,
          defaultAnimation: event.data.defaultAnimation,
          error: event.data.error,
        },
      }).finally(runNext);
    };
    worker.onerror = (event) => {
      const candidate = active;
      active = null;
      if (candidate) {
        void invoke("complete_user_avatar_model_validation", {
          result: {
            id: candidate.id,
            contentRevision: candidate.contentRevision,
            success: false,
            error: event.message,
          },
        }).finally(runNext);
      }
    };

    void invoke<UserModelScanSnapshot>("get_user_avatar_model_scan").then(enqueue);
    const unlistenPromise = listen<UserModelScanSnapshot>(
      "avatar-models://scan-updated",
      (event) => enqueue(event.payload),
    );
    return () => {
      cancelled = true;
      worker.terminate();
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, []);
}
