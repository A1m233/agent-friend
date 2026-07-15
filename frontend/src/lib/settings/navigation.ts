import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { isTauri } from "@/utils/tauri";

export const SETTINGS_NAVIGATE_EVENT = "settings://navigate";

export type SettingsTarget =
  | "general"
  | "persona"
  | "pet"
  | "voice"
  | "runtime"
  | "archived"
  | "feedback";

export interface SettingsOpenRequest {
  target?: SettingsTarget;
  feedback?: {
    preselectSessionId?: string;
  };
}

export interface SettingsNavigationEnvelope {
  requestId: number;
  request: SettingsOpenRequest;
}

export interface ResolvedSettingsLocation {
  section: SettingsTarget;
  preselectSessionId?: string;
}

const SETTINGS_TARGETS = new Set<SettingsTarget>([
  "general",
  "persona",
  "pet",
  "voice",
  "runtime",
  "archived",
  "feedback",
]);

export function locateSettings(
  request: SettingsOpenRequest | null | undefined,
): ResolvedSettingsLocation {
  const requestedTarget = request?.target;
  const section =
    requestedTarget && SETTINGS_TARGETS.has(requestedTarget)
      ? requestedTarget
      : "general";
  return {
    section,
    preselectSessionId:
      section === "feedback"
        ? request?.feedback?.preselectSessionId || undefined
        : undefined,
  };
}

export async function openSettings(request?: SettingsOpenRequest): Promise<void> {
  if (!isTauri()) return;
  await invoke("open_settings", { request: request ?? null });
}

export async function consumePendingSettingsNavigation(): Promise<
  SettingsNavigationEnvelope | null
> {
  if (!isTauri()) return null;
  return invoke<SettingsNavigationEnvelope | null>(
    "consume_pending_settings_navigation",
  );
}

export async function listenSettingsNavigation(
  listener: (envelope: SettingsNavigationEnvelope) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  return listen<SettingsNavigationEnvelope>(SETTINGS_NAVIGATE_EVENT, (event) => {
    listener(event.payload);
  });
}
