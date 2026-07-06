import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";

export type RuntimeConfigGroup = "deepseek" | "kimi" | "tavily" | "volcVoice";
export type RuntimeConfigKind = "secret";
export type RuntimeConfigSource = "env" | "keyring" | "legacy-dotenv";
export type RuntimeConfigEffect = "restartBridge" | "restartVoice";

export type RuntimeSecretName =
  | "DEEPSEEK_API_KEY"
  | "KIMI_API_KEY"
  | "TAVILY_API_KEY"
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

interface RuntimeConfigState {
  snapshot: RuntimeConfigSnapshot;
  loading: boolean;
  savingName: RuntimeSecretName | null;
  error: string | null;
}

const EMPTY_SNAPSHOT: RuntimeConfigSnapshot = { items: [] };

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

export function describeRuntimeConfigEffect(effect: RuntimeConfigEffect): string {
  switch (effect) {
    case "restartBridge":
      return "对话服务重启后生效";
    case "restartVoice":
      return "语音服务重启后生效";
  }
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
        const mutation = await setRuntimeSecret(name, value);
        setState((current) => ({
          ...current,
          snapshot: replaceItem(current.snapshot, mutation.item),
          savingName: null,
        }));
        toast.success("凭据已保存", {
          description: describeRuntimeConfigEffect(mutation.effect),
        });
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
      const mutation = await deleteRuntimeSecret(name);
      setState((current) => ({
        ...current,
        snapshot: replaceItem(current.snapshot, mutation.item),
        savingName: null,
      }));
      toast.success("凭据已删除", {
        description: describeRuntimeConfigEffect(mutation.effect),
      });
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
