import { beforeEach, describe, expect, it, vi } from "vitest";

const eventListeners: Array<(event: { payload: unknown }) => void> = [];
const unlisten = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (_name: string, listener: (event: { payload: unknown }) => void) => {
    eventListeners.push(listener);
    return unlisten;
  }),
}));

import { invoke } from "@tauri-apps/api/core";
import type { RuntimeCredentialGroupSnapshot } from "@/lib/runtime-config";
import {
  startRuntimeCredentialSync,
  useRuntimeCredentialStore,
} from "./runtimeCredentials";

const mockInvoke = vi.mocked(invoke);

function group(phase: RuntimeCredentialGroupSnapshot["operation"]["phase"]) {
  return {
    group: "deepseek" as const,
    items: [],
    configured: true,
    partiallyConfigured: false,
    missingRequiredFields: [],
    validation: "valid" as const,
    effect: "restartBridge" as const,
    operation: { phase, persisted: true, applied: phase === "applied" },
  } satisfies RuntimeCredentialGroupSnapshot;
}

describe("runtime credential store", () => {
  beforeEach(() => {
    eventListeners.length = 0;
    mockInvoke.mockReset();
    unlisten.mockReset();
    useRuntimeCredentialStore.setState({ groups: [], loading: false, error: null });
  });

  it("submits one grouped mutation and replaces the matching snapshot", async () => {
    mockInvoke.mockResolvedValue(group("applied"));

    await useRuntimeCredentialStore.getState().applyGroup("deepseek", [
      { name: "DEEPSEEK_API_KEY", action: "set", value: "fixture-secret" },
    ]);

    expect(mockInvoke).toHaveBeenCalledWith("apply_runtime_credential_group", {
      payload: {
        group: "deepseek",
        changes: [
          { name: "DEEPSEEK_API_KEY", action: "set", value: "fixture-secret" },
        ],
      },
    });
    expect(useRuntimeCredentialStore.getState().groups).toEqual([group("applied")]);
  });

  it("uses cross-window events as the authoritative operation update", async () => {
    mockInvoke.mockResolvedValue({ groups: [] });
    const stop = startRuntimeCredentialSync();
    await Promise.resolve();

    eventListeners[0]?.({ payload: group("restarting") });

    expect(useRuntimeCredentialStore.getState().groups[0]?.operation.phase).toBe(
      "restarting",
    );
    stop();
    await Promise.resolve();
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("reports runtime auth failure with the request-captured revision", async () => {
    mockInvoke.mockResolvedValue({
      ...group("applied"),
      validation: "invalid",
      validationErrorCode: "runtime_auth_failed",
    });

    await useRuntimeCredentialStore
      .getState()
      .reportAuthFailure("deepseek", "request-revision");

    expect(mockInvoke).toHaveBeenCalledWith(
      "report_runtime_credential_auth_failure",
      {
        payload: {
          group: "deepseek",
          credentialRevision: "request-revision",
        },
      },
    );
  });
});
