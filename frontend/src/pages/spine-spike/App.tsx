import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as PIXI8 from "pixi.js";
import { Assets as Assets8 } from "pixi.js";
import { Spine as Spine43 } from "@esotericsoftware/spine-pixi-v8";
import * as PIXI7 from "pixi7";
import { Spine as Spine38 } from "pixi-spine";
import {
  Activity,
  AlertTriangle,
  BoxSelect,
  Brain,
  Crosshair,
  Hand,
  MessageCircle,
  Moon,
  MousePointer2,
  Move,
  Play,
  RefreshCw,
  RotateCcw,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import {
  Badge,
  Button,
  Input,
  ScrollArea,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
} from "@/components/ui";
import type { PixelRegion } from "@/pet/petAlphaHitTest";
import { cn } from "@/utils/cn";
import {
  ARK_ACTION_MAPPING,
  ARK_SPINE_CAPABILITIES,
  RUNTIME_OPTIONS,
  chooseInitialAnimation,
  formatCapabilities,
  isPhaseAction,
  resolveAnimationName,
  type PetAvatarAction,
  type PetRuntimePhase,
  type RuntimeMode,
} from "./petAvatarRuntime";

const DEFAULT_SKELETON_URL = "/spine-spike-fixtures/ark-amiya/build_char_002_amiya.skel";
const DEFAULT_ATLAS_URL = "/spine-spike-fixtures/ark-amiya/build_char_002_amiya.atlas";
const DEFAULT_ANIMATION = "Default";
const DEFAULT_SCALE = "1";
const DEFAULT_RUNTIME_MODE: RuntimeMode = "spine38-pixi7";
const FIT_WIDTH_RATIO = 0.72;
const FIT_HEIGHT_RATIO = 0.82;
const SLOT_Y_RATIO = 0.86;
const ALPHA_THRESHOLD = 30;

const PHASE_BUTTONS: Array<{ phase: PetRuntimePhase; label: string; icon: LucideIcon }> = [
  { phase: "idle", label: "Idle", icon: Sparkles },
  { phase: "thinking", label: "Think", icon: Brain },
  { phase: "speaking", label: "Speak", icon: MessageCircle },
  { phase: "error", label: "Error", icon: AlertTriangle },
  { phase: "sleep", label: "Sleep", icon: Moon },
];

const ACTION_BUTTONS: Array<{
  action: Extract<PetAvatarAction, "tap" | "dragStart" | "dragEnd">;
  label: string;
  icon: LucideIcon;
}> = [
  { action: "tap", label: "Tap", icon: Hand },
  { action: "dragStart", label: "Drag", icon: Move },
  { action: "dragEnd", label: "Drop", icon: RotateCcw },
];

type StatusKind = "idle" | "loading" | "ready" | "error";

interface Status {
  kind: StatusKind;
  message: string;
}

interface Metrics {
  pointer: { x: number; y: number } | null;
  alphaHit: boolean | null;
  displayBounds: PixelRegion | null;
  visibleBounds: PixelRegion | null;
  slot: { x: number; y: number; scale: number } | null;
  fps: number | null;
}

type RuntimeApp = PIXI8.Application | PIXI7.Application<HTMLCanvasElement>;
type RuntimeSlot = PIXI8.Container | PIXI7.Container;
type RuntimeSpine = Spine43 | Spine38;

interface RuntimeRefs {
  mode: RuntimeMode;
  app: RuntimeApp | null;
  slot: RuntimeSlot | null;
  spine: RuntimeSpine | null;
}

interface Spine38Resource {
  spineData: ConstructorParameters<typeof Spine38>[0];
}

function parseRuntimeMode(value: string | null): RuntimeMode {
  return value === "spine38-pixi7" ? "spine38-pixi7" : DEFAULT_RUNTIME_MODE;
}

function readParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    runtimeMode: parseRuntimeMode(params.get("runtime")),
    skeleton: params.get("skeleton") ?? DEFAULT_SKELETON_URL,
    atlas: params.get("atlas") ?? DEFAULT_ATLAS_URL,
    animation: params.get("animation") ?? DEFAULT_ANIMATION,
    scale: params.get("scale") ?? DEFAULT_SCALE,
  };
}

function getAppCanvas(app: RuntimeApp | null): HTMLCanvasElement | null {
  if (!app) return null;

  const appCanvas = "canvas" in app ? app.canvas : "view" in app ? app.view : null;
  if (appCanvas instanceof HTMLCanvasElement) return appCanvas;

  const renderer = app.renderer as { canvas?: unknown; view?: unknown; resolution?: number };
  const rendererCanvas = renderer.canvas ?? renderer.view ?? null;
  return rendererCanvas instanceof HTMLCanvasElement ? rendererCanvas : null;
}

function getRendererResolution(app: RuntimeApp): number {
  const renderer = app.renderer as { resolution?: unknown };
  return typeof renderer.resolution === "number" ? renderer.resolution : 1;
}

function alphaHitTestForSpike(app: RuntimeApp | null, cursor: { x: number; y: number }): boolean {
  const canvas = getAppCanvas(app);
  if (!app || !canvas) return false;
  const gl = canvas.getContext("webgl2") as WebGL2RenderingContext | null;
  if (!gl) return false;

  const dpr = getRendererResolution(app);
  const cx = Math.round(cursor.x * dpr);
  const cy = Math.round(canvas.height - cursor.y * dpr);
  if (cx < 0 || cy < 0 || cx >= canvas.width || cy >= canvas.height) return false;

  const buf = new Uint8Array(4);
  try {
    gl.readPixels(cx, cy, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  } catch {
    return false;
  }
  return buf[3] > ALPHA_THRESHOLD;
}

function findVisibleBoundsForSpike(app: RuntimeApp | null, region: PixelRegion): PixelRegion | null {
  const canvas = getAppCanvas(app);
  if (!app || !canvas) return null;
  const gl = canvas.getContext("webgl2") as WebGL2RenderingContext | null;
  if (!gl) return null;

  const dpr = getRendererResolution(app);
  const px0 = Math.max(0, Math.floor(region.x * dpr));
  const py0 = Math.max(0, Math.floor(region.y * dpr));
  const px1 = Math.min(canvas.width, Math.ceil((region.x + region.w) * dpr));
  const py1 = Math.min(canvas.height, Math.ceil((region.y + region.h) * dpr));
  const w = px1 - px0;
  const h = py1 - py0;
  if (w <= 0 || h <= 0) return null;

  const buf = new Uint8Array(w * h * 4);
  try {
    gl.readPixels(px0, canvas.height - py1, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  } catch {
    return null;
  }

  let minX = w;
  let minY = h;
  let maxX = -1;
  let maxY = -1;
  for (let row = 0; row < h; row++) {
    const canvasRow = h - 1 - row;
    const rowOffset = row * w * 4;
    for (let col = 0; col < w; col++) {
      const alpha = buf[rowOffset + col * 4 + 3];
      if (alpha > ALPHA_THRESHOLD) {
        if (col < minX) minX = col;
        if (canvasRow < minY) minY = canvasRow;
        if (col > maxX) maxX = col;
        if (canvasRow > maxY) maxY = canvasRow;
      }
    }
  }
  if (maxX < 0) return null;

  return {
    x: (px0 + minX) / dpr,
    y: (py0 + minY) / dpr,
    w: (maxX - minX + 1) / dpr,
    h: (maxY - minY + 1) / dpr,
  };
}

function formatRegion(region: PixelRegion | null): string {
  if (!region) return "n/a";
  return `x=${region.x.toFixed(1)} y=${region.y.toFixed(1)} w=${region.w.toFixed(1)} h=${region.h.toFixed(1)}`;
}

function boundsToRegion(bounds: unknown): PixelRegion | null {
  const item = bounds as {
    x?: number;
    y?: number;
    minX?: number;
    minY?: number;
    width?: number;
    height?: number;
  };
  const x = typeof item.minX === "number" ? item.minX : item.x;
  const y = typeof item.minY === "number" ? item.minY : item.y;
  const w = item.width;
  const h = item.height;
  if (
    typeof x !== "number" ||
    typeof y !== "number" ||
    typeof w !== "number" ||
    typeof h !== "number" ||
    !Number.isFinite(x) ||
    !Number.isFinite(y) ||
    !Number.isFinite(w) ||
    !Number.isFinite(h)
  ) {
    return null;
  }
  return { x, y, w, h };
}

async function assertAssetReachable(url: string, label: string): Promise<void> {
  const response = await fetch(url, { method: "GET" });
  if (!response.ok) {
    throw new Error(`${label} URL returned HTTP ${response.status}: ${url}`);
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("text/html")) {
    throw new Error(
      `${label} URL resolved to HTML instead of a Spine asset. Check that the file exists under frontend/public/: ${url}`,
    );
  }
}

function setSlotHitArea(slot: RuntimeSlot, mode: RuntimeMode, region: PixelRegion): void {
  const rect =
    mode === "spine38-pixi7"
      ? new PIXI7.Rectangle(region.x, region.y, region.w, region.h)
      : new PIXI8.Rectangle(region.x, region.y, region.w, region.h);
  (slot as { hitArea: unknown }).hitArea = rect;
}

function addRuntimeChild(container: unknown, child: unknown): void {
  (container as { addChild: (item: unknown) => unknown }).addChild(child);
}

function removeRuntimeChild(container: unknown, child: unknown): void {
  (container as { removeChild: (item: unknown) => unknown }).removeChild(child);
}

function getGlobalPoint(event: unknown): { x: number; y: number } | null {
  const candidate = event as { global?: { x?: unknown; y?: unknown } };
  const x = candidate.global?.x;
  const y = candidate.global?.y;
  return typeof x === "number" && typeof y === "number" ? { x, y } : null;
}

function setupSlotDrag(slot: RuntimeSlot): void {
  let pointerStart: { x: number; y: number } | null = null;
  let slotStart: { x: number; y: number } | null = null;
  const emitter = slot as unknown as {
    on: (event: string, handler: (event: unknown) => void) => void;
  };

  emitter.on("pointerdown", (event) => {
    const global = getGlobalPoint(event);
    if (!global) return;
    pointerStart = global;
    slotStart = { x: slot.x, y: slot.y };
    slot.cursor = "grabbing";
  });
  emitter.on("globalpointermove", (event) => {
    const global = getGlobalPoint(event);
    if (!global || !pointerStart || !slotStart) return;
    slot.x = slotStart.x + global.x - pointerStart.x;
    slot.y = slotStart.y + global.y - pointerStart.y;
  });
  const endDrag = () => {
    pointerStart = null;
    slotStart = null;
    slot.cursor = "grab";
  };
  emitter.on("pointerup", endDrag);
  emitter.on("pointerupoutside", endDrag);
}

function getTickerDeltaMs(tickerArg: unknown): number {
  if (typeof tickerArg === "number") return tickerArg * (1000 / 60);
  const ticker = tickerArg as { deltaMS?: unknown };
  return typeof ticker.deltaMS === "number" ? ticker.deltaMS : 1000 / 60;
}

function fitSpineToStage(app: RuntimeApp, slot: RuntimeSlot, spine: RuntimeSpine): void {
  slot.scale.set(1);
  slot.position.set(app.screen.width / 2, app.screen.height * SLOT_Y_RATIO);
  spine.position.set(0, 0);
  spine.update(0);

  const localBounds = boundsToRegion(spine.getLocalBounds());
  if (!localBounds || localBounds.w <= 0 || localBounds.h <= 0) return;

  const fitScale = Math.min(
    (app.screen.width * FIT_WIDTH_RATIO) / localBounds.w,
    (app.screen.height * FIT_HEIGHT_RATIO) / localBounds.h,
  );
  const safeScale = Number.isFinite(fitScale) && fitScale > 0 ? fitScale : 1;
  slot.scale.set(safeScale);
  spine.position.set(
    -localBounds.x - localBounds.w / 2,
    -localBounds.y - localBounds.h,
  );
}

export function SpineSpikeApp() {
  const initial = useMemo(readParams, []);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const refs = useRef<RuntimeRefs>({
    mode: initial.runtimeMode,
    app: null,
    slot: null,
    spine: null,
  });
  const loadSeq = useRef(0);

  const [runtimeMode, setRuntimeMode] = useState<RuntimeMode>(initial.runtimeMode);
  const [skeletonUrl, setSkeletonUrl] = useState(initial.skeleton);
  const [atlasUrl, setAtlasUrl] = useState(initial.atlas);
  const [animationName, setAnimationName] = useState(initial.animation);
  const [tapAnimationName, setTapAnimationName] = useState("");
  const [assetScale, setAssetScale] = useState(initial.scale);
  const [animations, setAnimations] = useState<string[]>([]);
  const [runtimePhase, setRuntimePhase] = useState<PetRuntimePhase>("idle");
  const [lastAction, setLastAction] = useState<PetAvatarAction>("idle");
  const [lastMappedAnimation, setLastMappedAnimation] = useState("n/a");
  const [status, setStatus] = useState<Status>({
    kind: "idle",
    message: "Load a Spine skeleton and atlas to begin.",
  });
  const [metrics, setMetrics] = useState<Metrics>({
    pointer: null,
    alphaHit: null,
    displayBounds: null,
    visibleBounds: null,
    slot: null,
    fps: null,
  });

  const canPlay = status.kind === "ready" && refs.current.spine !== null;

  const updateMetrics = useCallback(() => {
    const app = refs.current.app;
    const slot = refs.current.slot;
    const spine = refs.current.spine;
    if (!app || !slot || !spine) return;
    const displayBounds = boundsToRegion(spine.getBounds());
    const visibleBounds = findVisibleBoundsForSpike(app, {
      x: 0,
      y: 0,
      w: app.screen.width,
      h: app.screen.height,
    });
    setMetrics((prev) => ({
      ...prev,
      displayBounds,
      visibleBounds,
      slot: { x: slot.x, y: slot.y, scale: slot.scale.x },
      fps: app.ticker.FPS,
    }));
    if (visibleBounds) {
      setSlotHitArea(slot, refs.current.mode, {
        x: (visibleBounds.x - slot.x) / slot.scale.x,
        y: (visibleBounds.y - slot.y) / slot.scale.y,
        w: visibleBounds.w / slot.scale.x,
        h: visibleBounds.h / slot.scale.y,
      });
    } else if (displayBounds) {
      setSlotHitArea(slot, refs.current.mode, {
        x: (displayBounds.x - slot.x) / slot.scale.x,
        y: (displayBounds.y - slot.y) / slot.scale.y,
        w: displayBounds.w / slot.scale.x,
        h: displayBounds.h / slot.scale.y,
      });
    }
  }, []);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    let cancelled = false;
    let app: RuntimeApp | null = null;
    let tickerElapsed = 0;

    void (async () => {
      let pixi: RuntimeApp;
      let slot: RuntimeSlot;
      if (runtimeMode === "spine38-pixi7") {
        pixi = new PIXI7.Application<HTMLCanvasElement>({
          backgroundAlpha: 0,
          resizeTo: stage,
          antialias: true,
          autoDensity: true,
          resolution: window.devicePixelRatio || 1,
          preserveDrawingBuffer: true,
        });
        slot = new PIXI7.Container();
        setSlotHitArea(slot, runtimeMode, { x: -1, y: -1, w: 2, h: 2 });
      } else {
        const pixi8 = new PIXI8.Application();
        await pixi8.init({
          backgroundAlpha: 0,
          resizeTo: stage,
          antialias: true,
          autoDensity: true,
          resolution: window.devicePixelRatio || 1,
          preserveDrawingBuffer: true,
        });
        pixi = pixi8;
        slot = new PIXI8.Container();
        setSlotHitArea(slot, runtimeMode, { x: -1, y: -1, w: 2, h: 2 });
      }
      if (cancelled) {
        if (runtimeMode === "spine38-pixi7") {
          (pixi as PIXI7.Application<HTMLCanvasElement>).destroy(true, {
            children: true,
            texture: true,
            baseTexture: true,
          });
        } else {
          (pixi as PIXI8.Application).destroy(true, { children: true, texture: true });
        }
        return;
      }

      app = pixi;
      const canvas = getAppCanvas(pixi);
      if (canvas) stage.appendChild(canvas);

      (slot as { label?: string }).label = "spine-spike-avatar-slot";
      slot.eventMode = "static";
      slot.cursor = "grab";
      addRuntimeChild(pixi.stage, slot);

      refs.current = { mode: runtimeMode, app: pixi, slot, spine: null };
      setAnimations([]);
      setRuntimePhase("idle");
      setLastAction("idle");
      setLastMappedAnimation("n/a");
      setMetrics({
        pointer: null,
        alphaHit: null,
        displayBounds: null,
        visibleBounds: null,
        slot: null,
        fps: null,
      });
      setStatus({
        kind: "idle",
        message: `Runtime ready: ${RUNTIME_OPTIONS.find((item) => item.value === runtimeMode)?.label}.`,
      });

      setupSlotDrag(slot);

      pixi.ticker.add((ticker) => {
        tickerElapsed += getTickerDeltaMs(ticker);
        if (tickerElapsed < 250) return;
        tickerElapsed = 0;
        updateMetrics();
      });
    })();

    return () => {
      cancelled = true;
      refs.current = { mode: runtimeMode, app: null, slot: null, spine: null };
      if (app) {
        try {
          const canvas = getAppCanvas(app);
          if (canvas && stage.contains(canvas)) stage.removeChild(canvas);
        } catch {
          /* DOM may already be detached */
        }
        if (runtimeMode === "spine38-pixi7") {
          (app as PIXI7.Application<HTMLCanvasElement>).destroy(true, {
            children: true,
            texture: true,
            baseTexture: true,
          });
        } else {
          (app as PIXI8.Application).destroy(true, { children: true, texture: true });
        }
      }
    };
  }, [runtimeMode, updateMetrics]);

  const loadModel = useCallback(async () => {
    const app = refs.current.app;
    const slot = refs.current.slot;
    if (!app || !slot) {
      setStatus({ kind: "error", message: "PIXI application is not ready yet." });
      return;
    }

    const skeleton = skeletonUrl.trim();
    const atlas = atlasUrl.trim();
    const scale = Number(assetScale);
    if (!skeleton || !atlas) {
      setStatus({ kind: "error", message: "Skeleton and atlas URLs are required." });
      return;
    }
    if (!Number.isFinite(scale) || scale <= 0) {
      setStatus({ kind: "error", message: "Scale must be a positive number." });
      return;
    }

    setStatus({ kind: "loading", message: "Loading Spine assets..." });
    setAnimations([]);
    setMetrics((prev) => ({ ...prev, displayBounds: null, visibleBounds: null }));

    const id = loadSeq.current++;
    const skeletonAlias = `spine-spike-skeleton-${id}`;
    const atlasAlias = `spine-spike-atlas-${id}`;

    try {
      await assertAssetReachable(skeleton, "Skeleton");
      await assertAssetReachable(atlas, "Atlas");

      let spine: RuntimeSpine;
      let runtimeMessage: string;
      if (runtimeMode === "spine38-pixi7") {
        PIXI7.Assets.add({
          alias: skeletonAlias,
          src: skeleton,
          data: { spineAtlasFile: atlas, spineSkeletonScale: scale },
        });
        const resource = await PIXI7.Assets.load<Spine38Resource>(skeletonAlias);
        spine = new Spine38(resource.spineData);
        runtimeMessage = "pixi-spine 4.0.6 / Pixi 7 / Spine 3.8-compatible.";
      } else {
        Assets8.add({ alias: skeletonAlias, src: skeleton });
        Assets8.add({ alias: atlasAlias, src: atlas });
        await Assets8.load([skeletonAlias, atlasAlias]);
        spine = new Spine43({
          skeleton: skeletonAlias,
          atlas: atlasAlias,
          scale,
          ticker: (app as PIXI8.Application).ticker,
        });
        spine.state.data.defaultMix = 0.15;
        runtimeMessage = "spine-pixi-v8 4.3.9.";
      }

      if (refs.current.spine) {
        removeRuntimeChild(slot, refs.current.spine);
        refs.current.spine.destroy({ children: true });
      }

      spine.eventMode = "none";
      addRuntimeChild(slot, spine);
      refs.current.spine = spine;

      const nextAnimations = spine.skeleton.data.animations.map((item) => item.name);
      const initialAnimation = chooseInitialAnimation(nextAnimations, animationName.trim());
      setAnimations(nextAnimations);
      if (initialAnimation) {
        setAnimationName(initialAnimation);
        spine.state.setAnimation(0, initialAnimation, true);
        setLastMappedAnimation(initialAnimation);
      }
      setRuntimePhase("idle");
      setLastAction("idle");

      fitSpineToStage(app, slot, spine);
      requestAnimationFrame(updateMetrics);
      setStatus({
        kind: "ready",
        message: `Loaded ${nextAnimations.length} animations. Runtime path: ${runtimeMessage}`,
      });
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, [animationName, assetScale, atlasUrl, runtimeMode, skeletonUrl, updateMetrics]);

  const playAnimation = useCallback((name: string, loop: boolean) => {
    const spine = refs.current.spine;
    if (!spine || !name) return;
    try {
      spine.state.setAnimation(0, name, loop);
      setStatus({ kind: "ready", message: `Playing ${name}${loop ? " looped" : ""}.` });
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, []);

  const playTapAnimation = useCallback(() => {
    const spine = refs.current.spine;
    if (!spine || !tapAnimationName) return;
    try {
      spine.state.setAnimation(0, tapAnimationName, false);
      if (animationName) spine.state.addAnimation(0, animationName, true, 0);
      setStatus({ kind: "ready", message: `Played one-shot ${tapAnimationName}.` });
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, [animationName, tapAnimationName]);

  const playPetAction = useCallback((action: PetAvatarAction) => {
    const spine = refs.current.spine;
    if (!spine) return;

    const mapping = ARK_ACTION_MAPPING[action];
    const target = resolveAnimationName(animations, mapping.animation, animationName);
    if (!target) {
      setStatus({ kind: "error", message: `No animation available for pet action ${action}.` });
      return;
    }

    try {
      if (isPhaseAction(action)) {
        setRuntimePhase(action);
        setAnimationName(target);
        spine.state.setAnimation(0, target, true);
        setLastAction(action);
        setLastMappedAnimation(target);
        setStatus({
          kind: "ready",
          message: `Pet phase ${action} mapped to ${target}.`,
        });
        return;
      }

      spine.state.setAnimation(0, target, mapping.loop);
      setLastAction(action);
      setLastMappedAnimation(target);

      if (mapping.returnToPhase) {
        const phaseMapping = ARK_ACTION_MAPPING[runtimePhase];
        const phaseAnimation = resolveAnimationName(animations, phaseMapping.animation, animationName);
        if (phaseAnimation) {
          spine.state.addAnimation(0, phaseAnimation, true, 0);
          setAnimationName(phaseAnimation);
        }
      }

      setStatus({
        kind: "ready",
        message: `Pet action ${action} mapped to ${target}${mapping.returnToPhase ? `, then ${runtimePhase}.` : "."}`,
      });
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }, [animationName, animations, runtimePhase]);

  const refit = useCallback(() => {
    const app = refs.current.app;
    const slot = refs.current.slot;
    const spine = refs.current.spine;
    if (!app || !slot || !spine) return;
    fitSpineToStage(app, slot, spine);
    updateMetrics();
  }, [updateMetrics]);

  const onStagePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const app = refs.current.app;
    if (!app) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const pointer = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
    setMetrics((prev) => ({
      ...prev,
      pointer,
      alphaHit: alphaHitTestForSpike(app, pointer),
    }));
  };

  const statusTone =
    status.kind === "ready"
      ? "default"
      : status.kind === "error"
        ? "destructive"
        : status.kind === "loading"
          ? "secondary"
          : "outline";

  return (
    <main className="flex h-screen min-h-0 bg-bg text-fg">
      <aside className="flex w-96 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border bg-surface p-4">
        <header className="grid gap-2">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-lg font-semibold">Spine Runtime Spike</h1>
              <p className="text-sm text-muted">Isolated Pixi slot loader for user-provided assets.</p>
            </div>
            <Badge variant={statusTone}>{status.kind}</Badge>
          </div>
          <p className={cn("text-sm", status.kind === "error" ? "text-danger" : "text-muted")}>
            {status.message}
          </p>
        </header>

        <section className="grid gap-3 rounded-md border border-border bg-bg p-4">
          <div className="flex items-center gap-2">
            <BoxSelect className="size-4 text-muted" />
            <h2 className="text-sm font-semibold">Assets</h2>
          </div>
          <label className="grid gap-2 text-sm">
            <span className="text-muted">Runtime</span>
            <Select
              value={runtimeMode}
              onValueChange={(value) => setRuntimeMode(parseRuntimeMode(value))}
              disabled={status.kind === "loading"}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RUNTIME_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-2 text-sm">
            <span className="text-muted">Skeleton URL</span>
            <Input value={skeletonUrl} onChange={(event) => setSkeletonUrl(event.target.value)} />
          </label>
          <label className="grid gap-2 text-sm">
            <span className="text-muted">Atlas URL</span>
            <Input value={atlasUrl} onChange={(event) => setAtlasUrl(event.target.value)} />
          </label>
          <label className="grid gap-2 text-sm">
            <span className="text-muted">Reader scale</span>
            <Input value={assetScale} onChange={(event) => setAssetScale(event.target.value)} />
          </label>
          <Button onClick={loadModel} disabled={status.kind === "loading"}>
            <RefreshCw />
            Load
          </Button>
        </section>

        <section className="grid gap-3 rounded-md border border-border bg-bg p-4">
          <div className="flex items-center gap-2">
            <Play className="size-4 text-muted" />
            <h2 className="text-sm font-semibold">Animation</h2>
          </div>
          <label className="grid gap-2 text-sm">
            <span className="text-muted">Loop animation</span>
            <Select value={animationName} onValueChange={setAnimationName} disabled={!animations.length}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="No animations loaded" />
              </SelectTrigger>
              <SelectContent>
                {animations.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-2 text-sm">
            <span className="text-muted">One-shot animation</span>
            <Select value={tapAnimationName} onValueChange={setTapAnimationName} disabled={!animations.length}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Choose after load" />
              </SelectTrigger>
              <SelectContent>
                {animations.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <Button disabled={!canPlay || !animationName} onClick={() => playAnimation(animationName, true)}>
              <Play />
              Play
            </Button>
            <Button variant="outline" disabled={!canPlay || !tapAnimationName} onClick={playTapAnimation}>
              <Activity />
              One-shot
            </Button>
            <Button variant="outline" disabled={!canPlay} onClick={refit}>
              <Crosshair />
              Fit
            </Button>
            <Button variant="outline" disabled={!canPlay} onClick={() => playAnimation(animationName, true)}>
              <RotateCcw />
              Idle
            </Button>
          </div>
        </section>

        <section className="grid gap-3 rounded-md border border-border bg-bg p-4">
          <div className="flex items-center gap-2">
            <Sparkles className="size-4 text-muted" />
            <h2 className="text-sm font-semibold">Pet Runtime</h2>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {PHASE_BUTTONS.map(({ phase, label, icon: Icon }) => (
              <Button
                key={phase}
                variant={runtimePhase === phase ? "default" : "outline"}
                disabled={!canPlay}
                onClick={() => playPetAction(phase)}
              >
                <Icon />
                {label}
              </Button>
            ))}
          </div>
          <Separator />
          <div className="grid grid-cols-3 gap-2">
            {ACTION_BUTTONS.map(({ action, label, icon: Icon }) => (
              <Button key={action} variant="outline" disabled={!canPlay} onClick={() => playPetAction(action)}>
                <Icon />
                {label}
              </Button>
            ))}
          </div>
        </section>

        <section className="min-h-0 rounded-md border border-border bg-bg p-4">
          <div className="mb-3 flex items-center gap-2">
            <MousePointer2 className="size-4 text-muted" />
            <h2 className="text-sm font-semibold">Observations</h2>
          </div>
          <Separator className="mb-3" />
          <ScrollArea className="h-64">
            <dl className="grid gap-2 pr-3 text-sm">
              <div>
                <dt className="text-muted">Animations</dt>
                <dd>{animations.length}</dd>
              </div>
              <div>
                <dt className="text-muted">Runtime phase</dt>
                <dd>{runtimePhase}</dd>
              </div>
              <div>
                <dt className="text-muted">Last action</dt>
                <dd>{lastAction}</dd>
              </div>
              <div>
                <dt className="text-muted">Mapped animation</dt>
                <dd>{lastMappedAnimation}</dd>
              </div>
              <div>
                <dt className="text-muted">Capabilities</dt>
                <dd className="break-words font-mono text-xs">{formatCapabilities(ARK_SPINE_CAPABILITIES)}</dd>
              </div>
              <div>
                <dt className="text-muted">FPS</dt>
                <dd>{metrics.fps ? metrics.fps.toFixed(1) : "n/a"}</dd>
              </div>
              <div>
                <dt className="text-muted">Pointer</dt>
                <dd>
                  {metrics.pointer
                    ? `${metrics.pointer.x.toFixed(1)}, ${metrics.pointer.y.toFixed(1)}`
                    : "n/a"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Alpha hit</dt>
                <dd>{metrics.alphaHit === null ? "n/a" : metrics.alphaHit ? "hit" : "transparent"}</dd>
              </div>
              <div>
                <dt className="text-muted">Display bounds</dt>
                <dd className="break-words font-mono text-xs">{formatRegion(metrics.displayBounds)}</dd>
              </div>
              <div>
                <dt className="text-muted">Visible bounds</dt>
                <dd className="break-words font-mono text-xs">{formatRegion(metrics.visibleBounds)}</dd>
              </div>
              <div>
                <dt className="text-muted">Slot</dt>
                <dd className="break-words font-mono text-xs">
                  {metrics.slot
                    ? `x=${metrics.slot.x.toFixed(1)} y=${metrics.slot.y.toFixed(1)} scale=${metrics.slot.scale.toFixed(3)}`
                    : "n/a"}
                </dd>
              </div>
            </dl>
          </ScrollArea>
        </section>
      </aside>

      <section
        ref={stageRef}
        className="relative min-w-0 flex-1 overflow-hidden bg-bg"
        onPointerMove={onStagePointerMove}
        onPointerLeave={() => setMetrics((prev) => ({ ...prev, pointer: null, alphaHit: null }))}
      >
        <div className="pointer-events-none absolute left-4 top-4 rounded-md border border-border bg-surface/90 px-3 py-2 text-xs text-muted shadow-sm">
          Drag the loaded Spine figure. Move over transparent gaps to test framebuffer alpha.
        </div>
      </section>
    </main>
  );
}
