/** Native coordinates are physical pixels; bubble CSS pixels belong to its own WebView. */
export interface BubbleMeasurement {
  revision: number;
  widthPhysical: number;
  heightPhysical: number;
  desiredWidthPhysical: number;
  desiredHeightPhysical: number;
  scaleFactor: number;
  showRequested: boolean;
  retainBounds: boolean;
}

export interface BubblePresentationState {
  stateRevision: number;
  layoutOwner: number;
  visible: boolean;
  applied: boolean;
  measurementRevision: number;
  layoutRevision: number;
  widthCss: number;
  heightCss: number;
  hostRectPhysical: PhysicalRect | null;
  targetRectPhysical: PhysicalRect | null;
  scaleFactor: number;
  retaining: boolean;
  carrierPlan: BubbleCarrierPlan | null;
}

/** Echo this plan to commit_bubble_carrier only after preparing viewport compensation. */
export interface BubbleCarrierPlan {
  owner: number;
  generation: number;
  layoutRevision: number;
  measurementRevision: number;
  rectPhysical: PhysicalRect;
}

export const BUBBLE_MEASUREMENT_EVENT = "pet://bubble-measurement";
export const BUBBLE_LAYOUT_APPLIED_EVENT = "bubble://layout-applied";

export interface PhysicalRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AccessoryMonitor extends PhysicalRect {
  workArea: PhysicalRect;
  scaleMilli: number;
}

export interface AccessoryContext {
  generation: number;
  origin: { x: number; y: number };
  monitors: AccessoryMonitor[];
}

export interface AccessoryLayoutSession {
  owner: number;
  context: AccessoryContext;
  measurement: BubbleMeasurement;
}
