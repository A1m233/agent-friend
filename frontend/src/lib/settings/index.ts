import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { toast } from "sonner";
import { DEFAULT_AVATAR_MODEL_ID } from "@/avatar-models";
import { syncDocumentTheme } from "@/lib/settings/theme";

export type ThemeMode = "light" | "dark";
export type BackgroundOnlinePolicy = "followSystem" | "pluggedIn" | "always";
export type DeepseekModel = "deepseek/deepseek-v4-flash" | "deepseek/deepseek-v4-pro";
export type ModelProvider = "deepseek" | "kimi";
export interface ModelSelection {
  provider: ModelProvider;
  model: string;
}
export type AvatarModelId = string;
export type PetAvatarSize = "normal" | "mini";
type LegacyPetDisplayMode = "normal" | "edgeRecall";

export interface Settings {
  theme: ThemeMode;
  backgroundOnlinePolicy: BackgroundOnlinePolicy;
  voiceTunnelConsentAccepted: boolean;
  immersiveMode: boolean;
  displayRecoveryProtectionEnabled: boolean;
  chatModel: ModelSelection;
  memoryModel: ModelSelection;
  avatarModelId: AvatarModelId;
  petAvatarSize: PetAvatarSize;
  avatarModelScalePercents: Record<string, number>;
  petMiniScalePercent: number;
}

export const DEFAULT_SETTINGS: Settings = {
  theme: "light",
  backgroundOnlinePolicy: "followSystem",
  voiceTunnelConsentAccepted: false,
  immersiveMode: true,
  displayRecoveryProtectionEnabled: false,
  chatModel: { provider: "kimi", model: "kimi/kimi-k2.5" },
  memoryModel: { provider: "deepseek", model: "deepseek/deepseek-v4-flash" },
  avatarModelId: DEFAULT_AVATAR_MODEL_ID,
  petAvatarSize: "normal",
  avatarModelScalePercents: {},
  petMiniScalePercent: 50,
};

export const AVATAR_MODEL_SCALE_MIN_PERCENT = 50;
export const AVATAR_MODEL_SCALE_MAX_PERCENT = 200;
export const PET_MINI_SCALE_MIN_PERCENT = 25;
export const PET_MINI_SCALE_MAX_PERCENT = 75;
export const AVATAR_SCALE_STEP_PERCENT = 1;

export function getAvatarModelScalePercent(
  scales: Readonly<Record<string, number>>,
  modelId: string,
): number {
  const value = scales[modelId] ?? 100;
  return Math.min(
    AVATAR_MODEL_SCALE_MAX_PERCENT,
    Math.max(AVATAR_MODEL_SCALE_MIN_PERCENT, value),
  );
}

const EVENT_CHANGED = "settings://changed";

type SettingsSnapshotPayload = Partial<Settings> & {
  deepseekModel?: DeepseekModel;
  deepseekMemoryModel?: DeepseekModel;
  petDisplayMode?: LegacyPetDisplayMode;
};

declare global {
  interface Window {
    __AGENT_FRIEND_SETTINGS__?: SettingsSnapshotPayload;
  }
}

function normalizeSettingsSnapshot(payload?: SettingsSnapshotPayload): Settings {
  const { petDisplayMode, deepseekModel, deepseekMemoryModel, ...current } =
    payload ?? {};
  const settings = { ...DEFAULT_SETTINGS, ...current };
  if (!payload?.chatModel && deepseekModel) {
    settings.chatModel = { provider: "deepseek", model: deepseekModel };
  }
  if (!payload?.memoryModel && deepseekMemoryModel) {
    settings.memoryModel = {
      provider: "deepseek",
      model: deepseekMemoryModel,
    };
  }
  if (!payload?.petAvatarSize && petDisplayMode === "edgeRecall") {
    settings.petAvatarSize = "mini";
  }
  return settings;
}

/** 启动期由 Rust facade plugin 的 js_init_script 同步注入，首帧即可用。 */
export function getSettingsSnapshot(): Settings {
  return normalizeSettingsSnapshot(
    typeof window === "undefined" ? undefined : window.__AGENT_FRIEND_SETTINGS__,
  );
}

/** 兼容旧调用名；新代码优先用 getSettingsSnapshot 表达"当前快照"。 */
export function getInitialSettings(): Settings {
  return getSettingsSnapshot();
}

export function applySettingCache<K extends keyof Settings>(
  key: K,
  value: Settings[K],
): void {
  window.__AGENT_FRIEND_SETTINGS__ = {
    ...getSettingsSnapshot(),
    [key]: value,
  };
}

export async function setSetting<K extends keyof Settings>(
  key: K,
  next: Settings[K],
): Promise<void> {
  const previous = getSettingsSnapshot()[key];
  applySettingCache(key, next);
  if (key === "theme") {
    syncDocumentTheme(next);
  }

  try {
    await invoke("set_setting", { payload: { key, value: next } });
  } catch (error) {
    applySettingCache(key, previous);
    if (key === "theme") {
      syncDocumentTheme(previous);
    }
    throw error;
  }
}

/**
 * 单 key 订阅 + 写入 hook。
 *
 * - 初值直接读 window.__AGENT_FRIEND_SETTINGS__，零异步、零闪屏。
 * - 监听主进程广播的 settings://changed，让其他窗口的写入反映到本窗口。
 * - 写入采用乐观更新：本地 state 立即变 → invoke 后端；失败时回滚并 toast。
 */
export function useSetting<K extends keyof Settings>(
  key: K,
): [Settings[K], (next: Settings[K]) => Promise<void>] {
  const [value, setValueState] = useState<Settings[K]>(() => getSettingsSnapshot()[key]);

  useEffect(() => {
    let mounted = true;
    const unlistenPromise = listen<{ key: string; value: Settings[K] }>(
      EVENT_CHANGED,
      (event) => {
        if (!mounted) return;
        if (event.payload.key === key) {
          setValueState(event.payload.value);
        }
      },
    );
    return () => {
      mounted = false;
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, [key]);

  const setValue = useCallback(
    async (next: Settings[K]) => {
      const previous = value;
      setValueState(next);
      try {
        await setSetting(key, next);
      } catch (error) {
        setValueState(previous);
        toast.error("设置保存失败", { description: String(error) });
      }
    },
    [key, value],
  );

  return [value, setValue];
}
