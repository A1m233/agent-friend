import type { PetViewport } from "./petDisplayProfile";

export interface PetWebviewDisplayContext {
  dpr: number;
  overlayViewport: PetViewport;
  monitorViewports: PetViewport[];
  topologyGeneration: number;
}

export function readWebviewDevicePixelRatio(): number {
  if (typeof window === "undefined") return 1;
  const dpr = window.devicePixelRatio;
  return Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
}
