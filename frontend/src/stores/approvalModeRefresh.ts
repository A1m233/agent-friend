import type { SessionApprovalModeState } from "@/services/api/sessions";

export type ApprovalModeRefreshResult = "applied" | "stale" | "failed";
export interface ApprovalModeRefreshOptions {
  invalidate?: boolean;
}

interface RefreshScope {
  sessionId: string | null;
  openToken: number;
  generation: number;
}

interface RefreshEntry {
  scope: RefreshScope;
  requested: number;
  promise: Promise<ApprovalModeRefreshResult>;
}

/** One approval GET per session, with a follow-up read for changes during the GET. */
export function createApprovalModeRefresher(dependencies: {
  current: () => Omit<RefreshScope, "generation">;
  load: (sessionId: string) => Promise<SessionApprovalModeState>;
  apply: (state: SessionApprovalModeState) => void;
}) {
  const pending = new Map<string, RefreshEntry>();
  let generation = 0;
  const currentScope = (): RefreshScope => ({ ...dependencies.current(), generation });
  const sameScope = (left: RefreshScope, right: RefreshScope) => (
    left.sessionId === right.sessionId
    && left.openToken === right.openToken
    && left.generation === right.generation
  );
  const isCurrent = (scope: RefreshScope) => sameScope(scope, currentScope());

  async function drain(sessionId: string, entry: RefreshEntry): Promise<ApprovalModeRefreshResult> {
    let outcome: ApprovalModeRefreshResult = "stale";
    try {
      while (isCurrent(entry.scope)) {
        const scope = entry.scope;
        const requested = entry.requested;
        try {
          const state = await dependencies.load(sessionId);
          if (!isCurrent(scope) || requested !== entry.requested) {
            outcome = "stale";
          } else if (state.session_id !== sessionId) {
            outcome = "failed";
          } else {
            dependencies.apply(state);
            outcome = "applied";
          }
        } catch {
          outcome = isCurrent(scope) && requested === entry.requested ? "failed" : "stale";
        }
        if (requested === entry.requested) break;
      }
      return outcome;
    } finally {
      if (pending.get(sessionId) === entry) pending.delete(sessionId);
    }
  }

  return {
    invalidate() {
      // Keep in-flight entries so a quick A -> B -> A never starts two A GETs.
      generation += 1;
    },
    async refresh(
      sessionId: string,
      options: ApprovalModeRefreshOptions = {},
    ): Promise<ApprovalModeRefreshResult> {
      const scope = currentScope();
      if (scope.sessionId !== sessionId) return "stale";
      let entry = pending.get(sessionId);
      if (entry) {
        if (options.invalidate || !sameScope(scope, entry.scope)) entry.requested += 1;
        entry.scope = scope;
      } else {
        entry = { scope, requested: 1, promise: Promise.resolve("stale") };
        pending.set(sessionId, entry);
        entry.promise = drain(sessionId, entry);
      }
      const result = await entry.promise;
      return isCurrent(scope) ? result : "stale";
    },
  };
}
