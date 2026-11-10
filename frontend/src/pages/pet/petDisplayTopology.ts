import type { MonitorPhysicalSnapshot, OverlayPhysicalBounds } from "./petMonitorViewport";

export interface PetDisplayTopologyChangedPayload {
  reason: string;
  generation: number;
  bounds: OverlayPhysicalBounds;
  previousBounds?: OverlayPhysicalBounds | null;
  previousMonitors?: MonitorPhysicalSnapshot[] | null;
  monitors: MonitorPhysicalSnapshot[];
}

export function createPetDisplayTopologyHandler(
  onGeneration: (generation: number) => void,
  onGeometryChanged: (payload: PetDisplayTopologyChangedPayload) => void,
) {
  let lastGeneration = 0;
  let lastSignature: string | null = null;
  return ({ payload }: { payload: PetDisplayTopologyChangedPayload }) => {
    const { generation, bounds, monitors } = payload;
    if (generation <= lastGeneration) return;
    lastGeneration = generation;
    // Native invalidates the old bubble placement on every new display generation.
    onGeneration(generation);
    const signature = JSON.stringify({ bounds, monitors });
    if (signature === lastSignature) return;
    lastSignature = signature;
    onGeometryChanged(payload);
  };
}
