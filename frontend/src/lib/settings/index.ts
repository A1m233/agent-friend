import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { toast } from "sonner";

export type ThemeMode = "light" | "dark";

export interface Settings {
  theme: ThemeMode;
  voiceTunnelConsentAccepted: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  theme: "light",
  voiceTunnelConsentAccepted: false,
};

const EVENT_CHANGED = "settings://changed";

declare global {
  interface Window {
    __AGENT_FRIEND_SETTINGS__?: Settings;
  }
}

/** 启动期由 Rust facade plugin 的 js_init_script 同步注入，首帧即可用。 */
export function getSettingsSnapshot(): Settings {
  return { ...DEFAULT_SETTINGS, ...(window.__AGENT_FRIEND_SETTINGS__ ?? {}) };
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
  try {
    await invoke("set_setting", { payload: { key, value: next } });
  } catch (error) {
    applySettingCache(key, previous);
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
