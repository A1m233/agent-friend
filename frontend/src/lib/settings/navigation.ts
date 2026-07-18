import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { isTauri } from "@/utils/tauri";

export const SETTINGS_NAVIGATE_EVENT = "settings://navigate";

export type SettingsTarget =
  | "general"
  | "persona"
  | "pet"
  | "voice"
  | "permissions"
  | "runtime"
  | "archived"
  | "feedback";

export type PrimarySettingsTarget = Exclude<SettingsTarget, "feedback">;

export const PRIMARY_SETTINGS_SECTIONS = [
  { key: "persona", label: "人格" },
  { key: "pet", label: "桌宠" },
  { key: "general", label: "通用" },
  { key: "voice", label: "语音" },
  { key: "runtime", label: "模型与凭据" },
  { key: "permissions", label: "系统权限" },
  { key: "archived", label: "已归档对话" },
] as const satisfies readonly {
  key: PrimarySettingsTarget;
  label: string;
}[];

export const DEFAULT_SETTINGS_TARGET: PrimarySettingsTarget =
  PRIMARY_SETTINGS_SECTIONS[0].key;

export interface SettingsOpenRequest {
  target?: SettingsTarget;
  feedback?: {
    preselectSessionId?: string;
  };
}

export interface OpenFeedbackOptions {
  preselectSessionId?: string;
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
  ...PRIMARY_SETTINGS_SECTIONS.map(({ key }) => key),
  "feedback",
]);

export function locateSettings(
  request: SettingsOpenRequest | null | undefined,
): ResolvedSettingsLocation {
  const requestedTarget = request?.target;
  const section =
    requestedTarget && SETTINGS_TARGETS.has(requestedTarget)
      ? requestedTarget
      : DEFAULT_SETTINGS_TARGET;
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

/** 打开统一的帮助与反馈页，并按需携带来源会话。 */
export async function openFeedback(options?: OpenFeedbackOptions): Promise<void> {
  await openSettings({
    target: "feedback",
    feedback: options?.preselectSessionId
      ? { preselectSessionId: options.preselectSessionId }
      : undefined,
  });
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
