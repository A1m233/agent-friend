import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { PetRect } from "@/pages/pet/petDisplayProfile";
import { isTauri } from "@/utils/tauri";
import { actionBarMetrics, actionBarMinimumMetrics, readAccessorySpacing } from "./actionBarMetrics";
import { computeAccessoryLayout, petCssToPhysical, selectAccessoryWorkArea, type AccessorySide } from "./geometry";
import { BUBBLE_MEASUREMENT_EVENT, type AccessoryContext, type AccessoryLayoutSession, type BubbleMeasurement } from "./protocol";

export function mergeBubbleMeasurement(previous: BubbleMeasurement | null, next: BubbleMeasurement): BubbleMeasurement {
  if (previous && previous.revision > next.revision) return previous;
  return previous && JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
}

/** One coordinator survives ActionBar hover unmounts; only the native bubble window applies pixels. */
export function usePetAccessoryLayout(input: {
  anchor: PetRect | null;
  fallbackViewport: PetRect;
  dpr: number;
  topologyGeneration: number;
  buttonCount: number;
  isDragging: boolean;
}) {
  const { anchor, fallbackViewport, dpr, topologyGeneration, buttonCount, isDragging } = input;
  const [owner, setOwner] = useState<number | null>(null);
  const [context, setContext] = useState<AccessoryContext | null>(null);
  const [measurement, setMeasurement] = useState<BubbleMeasurement | null>(null);
  const previousLayout = useRef<{ workAreaKey: string; side: AccessorySide } | null>(null);
  const revision = useRef(0);
  const previousApplication = useRef<{ key: string | null; cx: number; cy: number } | null>(null);
  const spacing = readAccessorySpacing();

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void (async () => {
      unlisten = await listen<BubbleMeasurement>(BUBBLE_MEASUREMENT_EVENT, (event) => {
        if (!cancelled) setMeasurement((prev) => mergeBubbleMeasurement(prev, event.payload));
      });
      if (cancelled) { unlisten(); return; }
      const session = await invoke<AccessoryLayoutSession>("begin_pet_accessory_layout");
      if (cancelled) return;
      setOwner(session.owner);
      setContext(session.context);
      setMeasurement((prev) => mergeBubbleMeasurement(prev, session.measurement));
    })().catch((error: unknown) => console.warn("[pet] accessory layout initialization failed", error));
    return () => { cancelled = true; unlisten?.(); };
  }, []);

  useEffect(() => {
    if (!owner || !isTauri()) return;
    let cancelled = false;
    void invoke<AccessoryContext>("get_pet_accessory_context").then((next) => {
      if (!cancelled) setContext((prev) => prev && prev.generation > next.generation ? prev : next);
    }).catch((error: unknown) => console.warn("[pet] accessory display context failed", error));
    return () => { cancelled = true; };
  }, [owner, topologyGeneration, dpr, fallbackViewport.w, fallbackViewport.h]);

  const workArea = useMemo(() => anchor
    ? context ? selectAccessoryWorkArea(anchor, context, dpr) : isTauri() ? null : fallbackViewport
    : null, [anchor, context, dpr, fallbackViewport]);
  // Edge hysteresis belongs to one coordinate space and display generation.
  const workAreaKey = workArea ? JSON.stringify([
    workArea.x, workArea.y, workArea.w, workArea.h,
    dpr, context?.generation, context?.origin.x, context?.origin.y,
  ]) : null;

  const layout = useMemo(() => {
    if (!anchor || !workArea) return null;
    return computeAccessoryLayout({
      anchor, workArea, sizes: actionBarMetrics(buttonCount, spacing),
      minimumSizes: actionBarMinimumMetrics(buttonCount, spacing),
      bubble: measurement?.showRequested
        ? { w: measurement.desiredWidthPhysical / dpr, h: measurement.desiredHeightPhysical / dpr }
        : null,
      // Native MIN_W / MIN_H are bubble logical pixels, not overlay logical pixels.
      minimumBubbleSize: { w: 240 * (measurement?.scaleFactor ?? dpr) / dpr, h: 64 * (measurement?.scaleFactor ?? dpr) / dpr },
      gap: spacing * 2,
      previousSide: previousLayout.current?.workAreaKey === workAreaKey ? previousLayout.current.side : null,
    });
  }, [anchor, workArea, workAreaKey, dpr, buttonCount, spacing, measurement]);

  useEffect(() => {
    previousLayout.current = layout && workAreaKey ? { workAreaKey, side: layout.side } : null;
  }, [layout, workAreaKey]);

  useEffect(() => {
    if (!owner || !context || !measurement || !isTauri()) return;
    // No frame/timer is added to the existing avatar -> React update path.
    const bubble = layout?.bubble && measurement.showRequested
      ? petCssToPhysical(layout.bubble, context.origin, dpr)
      : null;
    const cx = anchor ? anchor.x + anchor.w / 2 : 0;
    const cy = anchor ? anchor.y + anchor.h / 2 : 0;
    const previous = previousApplication.current;
    const retainBounds = measurement.retainBounds && !isDragging && previous?.key === workAreaKey
      && Math.abs(previous.cx - cx) < 1 && Math.abs(previous.cy - cy) < 1;
    previousApplication.current = { key: workAreaKey, cx, cy };
    void invoke("apply_pet_accessory_layout", {
      request: { owner, generation: context.generation, revision: ++revision.current, measurementRevision: measurement.revision, bubble, retainBounds },
    }).catch((error: unknown) => console.warn("[pet] native accessory layout failed", error));
  }, [owner, context, measurement, layout, dpr, anchor, workAreaKey, isDragging]);

  return layout;
}
