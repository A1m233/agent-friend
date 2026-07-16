import { invoke } from "@tauri-apps/api/core";

export interface AutostartStatus {
  supported: boolean;
  enabled: boolean;
  reason?: string | null;
}

export function getAutostartStatus(): Promise<AutostartStatus> {
  return invoke<AutostartStatus>("get_autostart_status");
}

export function setAutostartEnabled(enabled: boolean): Promise<AutostartStatus> {
  return invoke<AutostartStatus>("set_autostart_enabled", { enabled });
}

export function autostartErrorMessage(error: unknown): string {
  const raw = String(error);
  if (raw.startsWith("autostart_forbidden:")) {
    return "只能在设置中心修改开机自启。";
  }
  if (raw.startsWith("autostart_debug_disabled:")) {
    return "开机自启只能在正式安装版中设置。";
  }
  if (raw.startsWith("autostart_state_mismatch:")) {
    return "Windows 启动项没有按预期更新，请检查系统启动应用设置。";
  }
  if (raw.startsWith("autostart_query_failed:")) {
    return "无法读取 Windows 启动项，请稍后重试。";
  }
  return "无法更新 Windows 启动项，请稍后重试。";
}
