import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@/services/runtime", () => ({ restartLocalService: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";
import { restartLocalService } from "@/services/runtime";
import {
  deleteRuntimeSecret,
  persistBridgeRuntimeSetting,
  persistRuntimeConfigChange,
  runtimeCredentialApplyReason,
  runtimeCredentialOperationBusy,
  setRuntimeSecret,
  type RuntimeCredentialGroupSnapshot,
  type RuntimeConfigMutation,
} from "./index";

const mockInvoke = vi.mocked(invoke);
const mockRestartLocalService = vi.mocked(restartLocalService);

function mutation(
  name: "AMAP_WEBSERVICE_KEY" | "VOLC_ACCESS_KEY",
  effect: "restartBridge" | "restartVoice",
  source: "env" | "keyring" = "keyring",
): RuntimeConfigMutation {
  return {
    effect,
    item: {
      name,
      kind: "secret",
      group: name === "AMAP_WEBSERVICE_KEY" ? "amap" : "volcVoice",
      configured: true,
      source,
      effect,
    },
  };
}

describe("runtime config changes", () => {
  beforeEach(() => {
    mockInvoke.mockReset();
    mockRestartLocalService.mockReset();
    mockRestartLocalService.mockResolvedValue(undefined);
  });

  it("saves a Bridge credential before restarting Bridge", async () => {
    const persisted = mutation("AMAP_WEBSERVICE_KEY", "restartBridge");
    mockInvoke.mockResolvedValue(persisted);

    const result = await persistRuntimeConfigChange(
      () => setRuntimeSecret("AMAP_WEBSERVICE_KEY", "secret"),
      (value) => value.effect,
    );

    expect(mockInvoke).toHaveBeenCalledWith("set_runtime_secret", {
      payload: { name: "AMAP_WEBSERVICE_KEY", value: "secret" },
    });
    expect(mockRestartLocalService).toHaveBeenCalledWith("bridge", "request_settings_changed");
    expect(result).toEqual({
      persisted,
      applied: true,
      applyError: null,
    });
  });

  it("deletes a Voice credential before restarting Voice Bridge", async () => {
    const persisted = mutation("VOLC_ACCESS_KEY", "restartVoice");
    mockInvoke.mockResolvedValue(persisted);

    const result = await persistRuntimeConfigChange(
      () => deleteRuntimeSecret("VOLC_ACCESS_KEY"),
      (value) => value.effect,
    );

    expect(mockInvoke).toHaveBeenCalledWith("delete_runtime_secret", {
      payload: { name: "VOLC_ACCESS_KEY" },
    });
    expect(mockRestartLocalService).toHaveBeenCalledWith("voice", "request_settings_changed");
    expect(result.applied).toBe(true);
  });

  it("keeps a persisted change when restart is blocked", async () => {
    const persisted = mutation("VOLC_ACCESS_KEY", "restartVoice");
    mockRestartLocalService.mockRejectedValue(
      new Error("active or connecting voice call blocks restart"),
    );

    const result = await persistRuntimeConfigChange(
      async () => persisted,
      (value) => value.effect,
    );

    expect(result.persisted).toBe(persisted);
    expect(result.applied).toBe(false);
    expect(result.applyError).toContain("voice call blocks restart");
  });

  it("does not claim an env-overridden keyring value was applied", async () => {
    const persisted = mutation(
      "AMAP_WEBSERVICE_KEY",
      "restartBridge",
      "env",
    );

    const result = await persistRuntimeConfigChange(
      async () => persisted,
      (value) => value.effect,
      (value) =>
        value.item.source === "env" ? "environment override remains active" : null,
    );

    expect(mockRestartLocalService).not.toHaveBeenCalled();
    expect(result.applied).toBe(false);
    expect(result.applyError).toBe("environment override remains active");
  });

  it("applies a persisted model setting by restarting Bridge", async () => {
    const persist = vi.fn().mockResolvedValue(undefined);

    const result = await persistBridgeRuntimeSetting(persist);

    expect(persist).toHaveBeenCalledOnce();
    expect(mockRestartLocalService).toHaveBeenCalledWith("bridge", "request_settings_changed");
    expect(result.applied).toBe(true);
  });

  it("classifies a default conversation model restart as a new model epoch", async () => {
    const persist = vi.fn().mockResolvedValue(undefined);

    await persistBridgeRuntimeSetting(persist, "default_model_changed");

    expect(mockRestartLocalService).toHaveBeenCalledWith("bridge", "default_model_changed");
  });

  it("serializes configuration persistence and restarts", async () => {
    let releaseFirst!: () => void;
    const firstPersisted = new Promise<RuntimeConfigMutation>((resolve) => {
      releaseFirst = () => resolve(mutation("AMAP_WEBSERVICE_KEY", "restartBridge"));
    });
    const order: string[] = [];

    const first = persistRuntimeConfigChange(
      async () => {
        order.push("persist-first");
        return firstPersisted;
      },
      (value) => value.effect,
    );
    const second = persistRuntimeConfigChange(
      async () => {
        order.push("persist-second");
        return mutation("VOLC_ACCESS_KEY", "restartVoice");
      },
      (value) => value.effect,
    );

    await Promise.resolve();
    expect(order).toEqual(["persist-first"]);
    releaseFirst();
    await Promise.all([first, second]);
    expect(order).toEqual(["persist-first", "persist-second"]);
  });
});

function credentialGroup(
  phase: RuntimeCredentialGroupSnapshot["operation"]["phase"],
): RuntimeCredentialGroupSnapshot {
  return {
    group: "deepseek",
    items: [],
    configured: true,
    partiallyConfigured: false,
    missingRequiredFields: [],
    validation: "none",
    effect: "restartBridge",
    operation: {
      phase,
      persisted: phase === "restarting",
      applied: false,
      message: phase === "restarting" ? "正在重启对话服务" : null,
    },
  };
}

describe("runtime credential presentation", () => {
  it("treats only active operation phases as busy", () => {
    expect(runtimeCredentialOperationBusy(credentialGroup("validating").operation)).toBe(true);
    expect(runtimeCredentialOperationBusy(credentialGroup("restarting").operation)).toBe(true);
    expect(runtimeCredentialOperationBusy(credentialGroup("awaitingConfirmation").operation)).toBe(
      false,
    );
    expect(runtimeCredentialOperationBusy(credentialGroup("failed").operation)).toBe(false);
  });

  it("projects the concrete credential apply reason to affected windows", () => {
    expect(runtimeCredentialApplyReason([credentialGroup("restarting")], "restartBridge")).toBe(
      "正在重启对话服务",
    );
    expect(runtimeCredentialApplyReason([credentialGroup("idle")], "restartBridge")).toBeNull();
    expect(runtimeCredentialApplyReason([credentialGroup("restarting")], "restartVoice")).toBeNull();
  });
});
