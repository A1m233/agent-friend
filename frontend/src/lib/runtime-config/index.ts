import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";
import { restartLocalService } from "@/services/runtime";

export type RuntimeConfigGroup =
  | "amap"
  | "deepseek"
  | "kimi"
  | "openrouter"
  | "tavily"
  | "qWeather"
  | "volcVoice";
export type RuntimeConfigKind = "secret";
export type RuntimeConfigSource = "env" | "keyring" | "legacy-dotenv";
export type RuntimeConfigEffect = "restartBridge" | "restartVoice";

export type RuntimeSecretName =
  | "AMAP_WEBSERVICE_KEY"
  | "DEEPSEEK_API_KEY"
  | "KIMI_API_KEY"
  | "OPENROUTER_API_KEY"
  | "TAVILY_API_KEY"
  | "QWEATHER_API_KEY"
  | "QWEATHER_API_HOST"
  | "VOLC_ACCESS_KEY"
  | "VOLC_SECRET_KEY"
  | "VOLC_RTC_APP_ID"
  | "VOLC_RTC_APP_KEY"
  | "VOLC_SPEECH_APP_ID"
  | "VOLC_SPEECH_ACCESS_TOKEN"
  | "VOLC_SPEECH_API_KEY";

export interface RuntimeConfigItemStatus {
  name: RuntimeSecretName;
  kind: RuntimeConfigKind;
  group: RuntimeConfigGroup;
  configured: boolean;
  source?: RuntimeConfigSource | null;
  effect: RuntimeConfigEffect;
  error?: string | null;
}

export interface RuntimeConfigSnapshot {
  items: RuntimeConfigItemStatus[];
}

export interface RuntimeConfigMutation {
  item: RuntimeConfigItemStatus;
  effect: RuntimeConfigEffect;
}

export type RuntimeCredentialValidationStatus = "none" | "valid" | "invalid";
export type RuntimeCredentialOperationPhase =
  | "idle"
  | "staging"
  | "validating"
  | "awaitingConfirmation"
  | "persisting"
  | "restarting"
  | "applied"
  | "applyBlocked"
  | "failed";

export interface RuntimeCredentialOperation {
  operationId?: string | null;
  phase: RuntimeCredentialOperationPhase;
  safeCode?: string | null;
  message?: string | null;
  persisted: boolean;
  applied: boolean;
}

export interface RuntimeCredentialGroupSnapshot {
  group: RuntimeConfigGroup;
  items: RuntimeConfigItemStatus[];
  configured: boolean;
  partiallyConfigured: boolean;
  missingRequiredFields: RuntimeSecretName[];
  validation: RuntimeCredentialValidationStatus;
  validationCheckedAt?: string | null;
  validationErrorCode?: string | null;
  credentialRevision?: string | null;
  effect: RuntimeConfigEffect;
  operation: RuntimeCredentialOperation;
}

export interface RuntimeCredentialSnapshot {
  groups: RuntimeCredentialGroupSnapshot[];
}

export interface RuntimeCredentialFieldMutation {
  name: RuntimeSecretName;
  action: "set" | "delete";
  value?: string;
}

interface RuntimeConfigState {
  snapshot: RuntimeConfigSnapshot;
  loading: boolean;
  savingName: RuntimeSecretName | null;
  error: string | null;
}

export interface RuntimeConfigChangeResult<T> {
  persisted: T;
  applied: boolean;
  applyError: string | null;
}

const EMPTY_SNAPSHOT: RuntimeConfigSnapshot = { items: [] };
let runtimeConfigMutationTail: Promise<void> = Promise.resolve();

export async function getRuntimeConfigStatus(): Promise<RuntimeConfigSnapshot> {
  return invoke<RuntimeConfigSnapshot>("get_runtime_config_status");
}

export async function setRuntimeSecret(
  name: RuntimeSecretName,
  value: string,
): Promise<RuntimeConfigMutation> {
  return invoke<RuntimeConfigMutation>("set_runtime_secret", {
    payload: { name, value },
  });
}

export async function deleteRuntimeSecret(
  name: RuntimeSecretName,
): Promise<RuntimeConfigMutation> {
  return invoke<RuntimeConfigMutation>("delete_runtime_secret", {
    payload: { name },
  });
}

export async function getRuntimeCredentialGroups(): Promise<RuntimeCredentialSnapshot> {
  return invoke<RuntimeCredentialSnapshot>("get_runtime_credential_groups");
}

export async function applyRuntimeCredentialGroup(
  group: RuntimeConfigGroup,
  changes: RuntimeCredentialFieldMutation[],
): Promise<RuntimeCredentialGroupSnapshot> {
  return invoke<RuntimeCredentialGroupSnapshot>("apply_runtime_credential_group", {
    payload: { group, changes },
  });
}

export async function confirmRuntimeCredentialSave(
  operationId: string,
): Promise<RuntimeCredentialGroupSnapshot> {
  return invoke<RuntimeCredentialGroupSnapshot>("confirm_runtime_credential_save", {
    payload: { operationId },
  });
}

export async function cancelRuntimeCredentialOperation(
  operationId: string,
): Promise<RuntimeCredentialGroupSnapshot> {
  return invoke<RuntimeCredentialGroupSnapshot>("cancel_runtime_credential_operation", {
    payload: { operationId },
  });
}

export async function retryRuntimeCredentialApply(
  group: RuntimeConfigGroup,
): Promise<RuntimeCredentialGroupSnapshot> {
  return invoke<RuntimeCredentialGroupSnapshot>("retry_runtime_credential_apply", {
    payload: { group },
  });
}

export async function retryRuntimeCredentialValidation(
  group: RuntimeConfigGroup,
): Promise<RuntimeCredentialGroupSnapshot> {
  return invoke<RuntimeCredentialGroupSnapshot>("retry_runtime_credential_validation", {
    payload: { group },
  });
}

export async function reportRuntimeCredentialAuthFailure(
  group: RuntimeConfigGroup,
  credentialRevision: string,
): Promise<RuntimeCredentialGroupSnapshot> {
  return invoke<RuntimeCredentialGroupSnapshot>("report_runtime_credential_auth_failure", {
    payload: { group, credentialRevision },
  });
}

export function runtimeCredentialOperationBusy(
  operation: RuntimeCredentialOperation,
): boolean {
  return ["staging", "validating", "persisting", "restarting"].includes(
    operation.phase,
  );
}

export function runtimeCredentialApplyReason(
  groups: RuntimeCredentialGroupSnapshot[],
  effect: RuntimeConfigEffect,
): string | null {
  const active = groups.find(
    (group) =>
      group.effect === effect && runtimeCredentialOperationBusy(group.operation),
  );
  if (!active) return null;
  if (active.operation.message) return active.operation.message;
  return effect === "restartBridge"
    ? "正在应用凭据并重启对话服务"
    : "正在应用凭据并重启语音服务";
}

function replaceItem(
  snapshot: RuntimeConfigSnapshot,
  item: RuntimeConfigItemStatus,
): RuntimeConfigSnapshot {
  const found = snapshot.items.some((current) => current.name === item.name);
  return {
    items: found
      ? snapshot.items.map((current) =>
          current.name === item.name ? item : current,
        )
      : [...snapshot.items, item],
  };
}

export function applyRuntimeConfigEffect(
  effect: RuntimeConfigEffect,
): Promise<void> {
  return restartLocalService(effect === "restartBridge" ? "bridge" : "voice");
}

/**
 * 串行执行“持久化 -> 应用”流程。应用失败不回滚已持久化配置，
 * 由调用方明确呈现“已保存但尚未应用”。
 */
export function persistRuntimeConfigChange<T>(
  persist: () => Promise<T>,
  effectFor: (persisted: T) => RuntimeConfigEffect,
  applyBlockReason?: (persisted: T) => string | null,
): Promise<RuntimeConfigChangeResult<T>> {
  const run = runtimeConfigMutationTail.then(async () => {
    const persisted = await persist();
    const blocked = applyBlockReason?.(persisted) ?? null;
    if (blocked) {
      return { persisted, applied: false, applyError: blocked };
    }
    try {
      await applyRuntimeConfigEffect(effectFor(persisted));
      return { persisted, applied: true, applyError: null };
    } catch (error) {
      return { persisted, applied: false, applyError: String(error) };
    }
  });
  runtimeConfigMutationTail = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

export function persistBridgeRuntimeSetting(
  persist: () => Promise<void>,
): Promise<RuntimeConfigChangeResult<void>> {
  return persistRuntimeConfigChange(persist, () => "restartBridge");
}

export function describeRuntimeConfigEffect(effect: RuntimeConfigEffect): string {
  switch (effect) {
    case "restartBridge":
      return "保存后自动重启对话服务";
    case "restartVoice":
      return "保存后自动重启语音服务";
  }
}

function appliedDescription(effect: RuntimeConfigEffect): string {
  return effect === "restartBridge"
    ? "对话服务已自动重启"
    : "语音服务已自动重启";
}

export function useRuntimeConfig() {
  const [state, setState] = useState<RuntimeConfigState>({
    snapshot: EMPTY_SNAPSHOT,
    loading: true,
    savingName: null,
    error: null,
  });

  const refresh = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const snapshot = await getRuntimeConfigStatus();
      setState((current) => ({
        ...current,
        snapshot,
        loading: false,
        error: null,
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        error: String(error),
      }));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const saveSecret = useCallback(
    async (name: RuntimeSecretName, value: string) => {
      setState((current) => ({ ...current, savingName: name, error: null }));
      try {
        const result = await persistRuntimeConfigChange(
          () => setRuntimeSecret(name, value),
          (mutation) => mutation.effect,
          (mutation) =>
            mutation.item.source === "env"
              ? "当前生效值来自环境变量；已保存 keyring 值，但需移除环境变量覆盖后才能应用。"
              : null,
        );
        const mutation = result.persisted;
        setState((current) => ({
          ...current,
          snapshot: replaceItem(current.snapshot, mutation.item),
          savingName: null,
          error: result.applied
            ? null
            : `凭据已保存，但尚未应用：${result.applyError}`,
        }));
        if (result.applied) {
          toast.success("凭据已保存并生效", {
            description: appliedDescription(mutation.effect),
          });
        } else {
          toast.warning("凭据已保存，但尚未应用", {
            description: result.applyError ?? undefined,
          });
        }
        return mutation;
      } catch (error) {
        setState((current) => ({
          ...current,
          savingName: null,
          error: String(error),
        }));
        toast.error("凭据保存失败", { description: String(error) });
        throw error;
      }
    },
    [],
  );

  const removeSecret = useCallback(async (name: RuntimeSecretName) => {
    setState((current) => ({ ...current, savingName: name, error: null }));
    try {
      const result = await persistRuntimeConfigChange(
        () => deleteRuntimeSecret(name),
        (mutation) => mutation.effect,
      );
      const mutation = result.persisted;
      setState((current) => ({
        ...current,
        snapshot: replaceItem(current.snapshot, mutation.item),
        savingName: null,
        error: result.applied
          ? null
          : `凭据已删除，但尚未应用：${result.applyError}`,
      }));
      if (result.applied) {
        toast.success("凭据已删除并生效", {
          description: appliedDescription(mutation.effect),
        });
      } else {
        toast.warning("凭据已删除，但尚未应用", {
          description: result.applyError ?? undefined,
        });
      }
      return mutation;
    } catch (error) {
      setState((current) => ({
        ...current,
        savingName: null,
        error: String(error),
      }));
      toast.error("凭据删除失败", { description: String(error) });
      throw error;
    }
  }, []);

  return {
    ...state,
    refresh,
    saveSecret,
    removeSecret,
  };
}
