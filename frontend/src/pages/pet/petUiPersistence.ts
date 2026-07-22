import { invoke } from "@tauri-apps/api/core";

import type { PetFacing } from "@/pet/avatarRuntime/facing";
import { isTauri } from "@/utils/tauri";
import type { PetMonitorRelativeAnchor } from "./petMonitorViewport";

export interface PetUiPersistence {
  anchor?: PetMonitorRelativeAnchor | null;
  facing?: PetFacing;
}

export async function loadPetUiPersistence(): Promise<PetUiPersistence> {
  if (!isTauri()) return {};
  return (await invoke("get_pet_ui_persistence")) as PetUiPersistence;
}

export async function savePetMonitorAnchor(
  anchor: PetMonitorRelativeAnchor | null,
): Promise<void> {
  if (!isTauri()) return;
  await invoke("set_pet_monitor_anchor", { payload: { anchor } });
}

export async function savePetFacing(facing: PetFacing): Promise<void> {
  if (!isTauri()) return;
  await invoke("set_pet_facing", { payload: { facing } });
}
