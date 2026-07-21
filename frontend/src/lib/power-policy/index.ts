import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type PowerSource = "ac" | "battery" | "unknown";
export type PowerDemandReason = "agentTask" | "imOnline";
export type PlatformCapability = "supported" | "unsupported";
export type InhibitorState = "inactive" | "active" | "error";
export type BridgeDemandState = "available" | "unavailable";
export type DesktopPlatform = "windows" | "macos" | "linux" | "other";

export interface PowerPolicyError {
  code: string;
  message: string;
}

export interface PowerPolicySnapshot {
  platform: DesktopPlatform;
  policy: "followSystem" | "pluggedIn" | "always";
  powerSource: PowerSource;
  reasons: PowerDemandReason[];
  capability: PlatformCapability;
  inhibitorState: InhibitorState;
  bridgeState: BridgeDemandState;
  lastError: PowerPolicyError | null;
}

export interface PowerPolicyPresentation {
  tone: "success" | "warning" | "danger" | "muted";
  label: string;
  detail: string;
}

const EVENT_CHANGED = "power-policy://changed";

export function getPowerPolicySnapshot(): Promise<PowerPolicySnapshot> {
  return invoke("get_power_policy_snapshot");
}

export function refreshPowerPolicy(): Promise<PowerPolicySnapshot> {
  return invoke("refresh_power_policy");
}

export function openPowerSettings(): Promise<void> {
  return invoke("open_power_settings");
}

export function listenPowerPolicy(
  listener: (snapshot: PowerPolicySnapshot) => void,
): Promise<UnlistenFn> {
  return listen<PowerPolicySnapshot>(EVENT_CHANGED, ({ payload }) => listener(payload));
}

export function powerSourceLabel(source: PowerSource): string {
  if (source === "ac") return "已接通电源";
  if (source === "battery") return "正在使用电池";
  return "无法确认供电状态";
}

export function reasonLabels(reasons: readonly PowerDemandReason[]): string[] {
  return reasons.map((reason) =>
    reason === "agentTask" ? "Agent 正在执行任务" : "QQ 保持在线",
  );
}

export function describePowerPolicy(
  snapshot: PowerPolicySnapshot,
): PowerPolicyPresentation {
  if (snapshot.capability === "unsupported") {
    return {
      tone: "warning",
      label: "当前平台暂不支持应用防休眠",
      detail: "已保留你的策略选择，但不会显示为已生效。",
    };
  }
  if (snapshot.inhibitorState === "error" || snapshot.lastError?.code.includes("inhibit")) {
    return {
      tone: "danger",
      label: "保持唤醒失败",
      detail: "应用会自动重试；你也可以检查系统电源设置。",
    };
  }
  if (snapshot.inhibitorState === "active") {
    const reasons = reasonLabels(snapshot.reasons).join("、");
    return {
      tone: "success",
      label: "正在保持系统唤醒",
      detail: reasons || "后台任务仍需保持在线。",
    };
  }
  if (snapshot.policy === "followSystem") {
    return {
      tone: "muted",
      label: "当前跟随系统电源设置",
      detail: "agent-friend 不会主动阻止系统休眠。",
    };
  }
  if (snapshot.bridgeState === "unavailable") {
    return {
      tone: "warning",
      label: "后台服务状态未知",
      detail: "暂时无法确认 Agent 与 IM 是否需要保持在线，应用会自动重试。",
    };
  }
  if (snapshot.reasons.length === 0) {
    return {
      tone: "muted",
      label: "当前无需保持唤醒",
      detail: "没有正在执行的 Agent 任务或需要持续在线的 QQ 绑定。",
    };
  }
  if (snapshot.policy === "pluggedIn" && snapshot.powerSource === "battery") {
    return {
      tone: "muted",
      label: "当前使用电池，按策略跟随系统",
      detail: reasonLabels(snapshot.reasons).join("、"),
    };
  }
  if (snapshot.policy === "pluggedIn" && snapshot.powerSource === "unknown") {
    return {
      tone: "warning",
      label: "无法确认供电状态，未主动阻止休眠",
      detail: "确认设备已接电后可重试，或改用“始终保持在线”。",
    };
  }
  return {
    tone: "muted",
    label: "当前未保持系统唤醒",
    detail: "应用正在重新校准状态。",
  };
}
