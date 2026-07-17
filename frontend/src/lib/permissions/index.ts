import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useSyncExternalStore } from "react";
import { isTauri } from "@/utils/tauri";

export type PermissionId = "microphone";
export type SystemPermissionState =
  | "not_determined"
  | "granted"
  | "denied"
  | "restricted"
  | "unsupported"
  | "unknown";
export type PermissionPresentationState =
  | "not_requested"
  | "granted"
  | "denied"
  | "revoked"
  | "restricted"
  | "unsupported"
  | "unknown";
export type PermissionRecoveryAction =
  | "request"
  | "retry_request"
  | "open_system_settings"
  | "none";

export interface PermissionItemSnapshot {
  id: PermissionId;
  definitionRevision: number;
  systemState: SystemPermissionState;
  presentationState: PermissionPresentationState;
  recoveryAction: PermissionRecoveryAction;
  handledForCurrentRevision: boolean;
  deferredForCurrentRevision: boolean;
  changedAtMs: number | null;
}

export interface PermissionFlowSnapshot {
  flowId: string;
  phase: "awaiting_user" | "requesting" | "refreshing";
  permissionIds: PermissionId[];
  currentPermissionId: PermissionId | null;
  joinedRequestIds: string[];
}

export type PermissionFlowReason =
  | "startup"
  | "upgrade"
  | "revoked"
  | "feature_fallback"
  | "manual";

export interface PermissionFlowRequest {
  requestId: string;
  reason: PermissionFlowReason;
  permissionIds: PermissionId[];
  sourceWindow?: string | null;
}

export interface PermissionFlowTicket {
  requestId: string;
  flowId: string | null;
  status: "started" | "joined" | "queued" | "noop";
}

export type PermissionRequestInstruction =
  | "native"
  | "web_media"
  | "open_settings"
  | "not_available";

export interface PermissionFlowFinished {
  flowId: string;
  requestIds: string[];
  grantedPermissionIds: PermissionId[];
}

export interface PermissionSnapshot {
  snapshotRevision: number;
  refreshing: boolean;
  activeFlow: PermissionFlowSnapshot | null;
  items: PermissionItemSnapshot[];
}

export type PermissionRefreshReason =
  | "startup"
  | "resumed"
  | "focused"
  | "manual"
  | "attempt_completed";

export const EMPTY_PERMISSION_SNAPSHOT: PermissionSnapshot = {
  snapshotRevision: 0,
  refreshing: false,
  activeFlow: null,
  items: [],
};

const EVENT_CHANGED = "permissions://changed";
const subscribers = new Set<() => void>();
let currentSnapshot = EMPTY_PERMISSION_SNAPSHOT;
let initialization: Promise<void> | null = null;
const STARTUP_STATE_RETRY_COUNT = 20;
const STARTUP_STATE_RETRY_DELAY_MS = 50;

export function acceptPermissionSnapshot(snapshot: PermissionSnapshot): boolean {
  if (snapshot.snapshotRevision <= currentSnapshot.snapshotRevision) return false;
  currentSnapshot = snapshot;
  subscribers.forEach((subscriber) => subscriber());
  return true;
}

export function getPermissionSnapshot(): PermissionSnapshot {
  return currentSnapshot;
}

export function subscribePermissionSnapshot(subscriber: () => void): () => void {
  subscribers.add(subscriber);
  return () => subscribers.delete(subscriber);
}

export async function loadInitialPermissionSnapshot(
  loader: () => Promise<PermissionSnapshot> = () =>
    invoke<PermissionSnapshot>("get_permission_snapshot"),
  wait: (delayMs: number) => Promise<void> = (delayMs) =>
    new Promise((resolve) => globalThis.setTimeout(resolve, delayMs)),
): Promise<PermissionSnapshot> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await loader();
    } catch (error) {
      const managerNotReady = String(error).includes("state not managed");
      if (!managerNotReady || attempt >= STARTUP_STATE_RETRY_COUNT - 1) throw error;
      await wait(STARTUP_STATE_RETRY_DELAY_MS);
    }
  }
}

export function initializePermissionSync(): Promise<void> {
  if (!isTauri()) return Promise.resolve();
  if (initialization) return initialization;

  initialization = (async () => {
    await listen<PermissionSnapshot>(EVENT_CHANGED, (event) => {
      acceptPermissionSnapshot(event.payload);
    });
    const snapshot = await loadInitialPermissionSnapshot();
    acceptPermissionSnapshot(snapshot);
  })().catch((error) => {
    initialization = null;
    console.error("permission sync initialization failed", error);
  });
  return initialization;
}

export async function refreshPermissions(
  reason: PermissionRefreshReason = "manual",
): Promise<PermissionSnapshot> {
  const snapshot = await invoke<PermissionSnapshot>("refresh_permissions", { reason });
  acceptPermissionSnapshot(snapshot);
  return snapshot;
}

export async function requestPermissionFlow(
  input: PermissionFlowRequest,
): Promise<PermissionFlowTicket> {
  return invoke<PermissionFlowTicket>("request_permission_flow", { input });
}

export async function beginPermissionAttempt(
  flowId: string,
  permissionId: PermissionId,
): Promise<PermissionRequestInstruction> {
  return invoke<PermissionRequestInstruction>("begin_permission_attempt", {
    flowId,
    permissionId,
  });
}

export async function completePermissionAttempt(
  flowId: string,
  permissionId: PermissionId,
): Promise<PermissionSnapshot> {
  const snapshot = await invoke<PermissionSnapshot>("complete_permission_attempt", {
    flowId,
    permissionId,
  });
  acceptPermissionSnapshot(snapshot);
  return snapshot;
}

export async function deferPermissionFlow(flowId: string): Promise<PermissionSnapshot> {
  const snapshot = await invoke<PermissionSnapshot>("defer_permission_flow", { flowId });
  acceptPermissionSnapshot(snapshot);
  return snapshot;
}

export async function openPermissionSystemSettings(
  permissionId: PermissionId,
): Promise<void> {
  await invoke("open_permission_system_settings", { permissionId });
}

export async function listenForPermissionFlowFinished(
  listener: (finished: PermissionFlowFinished) => void,
): Promise<() => void> {
  return listen<PermissionFlowFinished>("permissions://flow-finished", (event) => {
    listener(event.payload);
  });
}

export function usePermissionSnapshot(): PermissionSnapshot {
  return useSyncExternalStore(
    subscribePermissionSnapshot,
    getPermissionSnapshot,
    getPermissionSnapshot,
  );
}

export function usePermission(id: PermissionId): PermissionItemSnapshot | undefined {
  return usePermissionSnapshot().items.find((item) => item.id === id);
}

export function hasPermissions(ids: PermissionId[]): boolean {
  const granted = new Set(
    currentSnapshot.items
      .filter((item) => item.systemState === "granted")
      .map((item) => item.id),
  );
  return ids.every((id) => granted.has(id));
}

export function __resetPermissionSyncForTests(): void {
  currentSnapshot = EMPTY_PERMISSION_SNAPSHOT;
  initialization = null;
  subscribers.clear();
}
