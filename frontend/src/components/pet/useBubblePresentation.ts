import { useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import { recordNotificationPresented } from "@/stores/notifications";
import { readAccessorySpacing } from "@/pet/accessoryLayout/actionBarMetrics";
import {
  BUBBLE_LAYOUT_APPLIED_EVENT,
  type BubbleMeasurement,
  type BubblePresentationState,
} from "@/pet/accessoryLayout/protocol";
import { BUBBLE_FRAME_PADDING, BUBBLE_WINDOW_WIDTH } from "./bubbleWindowSize";
import { acquireBubbleWindowVisibility } from "./bubbleWindowVisibility";
import { bubbleFrame } from "./bubbleFrame";

// One command lane survives effect cleanup/setup; queued obsolete sizes are skipped.
let bubbleMeasurementLane: Promise<unknown> = Promise.resolve();

export function isNotificationVisible(element: HTMLElement, state: BubblePresentationState | null, revision: number): boolean {
  if (!state?.visible || !state.applied || state.measurementRevision < revision) return false;
  const item = element.closest<HTMLElement>("[data-bubble-item]");
  const viewport = element.closest<HTMLElement>("[data-bubble-viewport]");
  if (!item || !viewport || item.dataset.state === "exiting" || viewport.dataset.transitioning !== "false") return false;
  const content = element.closest<HTMLElement>("[data-notification-content]") ?? element;
  const rect = content.getBoundingClientRect();
  const bounds = viewport.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && rect.top >= Math.max(0, bounds.top)
    && rect.left >= Math.max(0, bounds.left) && rect.bottom <= Math.min(state.heightCss, bounds.bottom)
    && rect.right <= Math.min(state.widthCss, bounds.right)
    && Number.parseFloat(getComputedStyle(item).opacity || "1") >= 0.99;
}

/** The controlled stack, its exit animations and the native carrier share one lifecycle. */
export function useBubblePresentation({ onExitComplete }: { onExitComplete?: (ids: string[]) => void } = {}): void {
  useEffect(() => {
    if (!isTauri()) return;
    let stopped = false;
    let raf = 0;
    let token = 0;
    let revision = Number.POSITIVE_INFINITY;
    let native: BubblePresentationState | null = null;
    let unlisten: (() => void) | undefined;
    let key = "";
    let pending = false;
    let running = false;
    let plannedTarget = "";
    let preparedCarrier = "";
    let domRevision = 0;
    let dirty = true;
    let completeAnimation: (() => Promise<void>) | null = null;
    let size = { width: BUBBLE_WINDOW_WIDTH, height: 0 };
    const known = new Set<string>();
    const finishedExits = new Set<string>();
    const observed = new Set<Element>();
    const animations = new Set<Animation>();
    const visibility = acquireBubbleWindowVisibility();
    const viewport = () => document.querySelector<HTMLElement>("[data-bubble-viewport]");
    const items = () => [...document.querySelectorAll<HTMLElement>("[data-bubble-item]")];
    // offsetHeight rounds down fractional text boxes on some WebView scales.
    const height = (node: HTMLElement) => Math.ceil(node.getBoundingClientRect().height);
    const cancelAnimations = () => { for (const animation of animations) animation.cancel(); animations.clear(); };
    const acknowledge = () => {
      if (dirty || pending || running) return;
      for (const element of document.querySelectorAll<HTMLElement>("[data-notification-id]")) {
        if (isNotificationVisible(element, native, revision)) {
          void recordNotificationPresented(element.dataset.notificationId!, element.textContent ?? "", element.dataset.truncated === "true");
        }
      }
    };
    const sendSize = (retainBounds: boolean, currentToken: number) => {
      const requestedSize = { ...size };
      const operation = bubbleMeasurementLane.then(() => stopped || currentToken !== token ? null
        : invoke<BubbleMeasurement>("set_bubble_size", { ...requestedSize, retainBounds }));
      bubbleMeasurementLane = operation.catch(() => undefined);
      return operation;
    };
    const setFrame = (state: BubblePresentationState) => {
      const frame = bubbleFrame(state);
      const root = viewport();
      if (!frame || !root) return;
      // These offsets become zero automatically when native bounds equal target.
      root.style.left = `calc(${BUBBLE_FRAME_PADDING}px + ${frame.x} * (100vw - ${frame.width}px))`;
      root.style.top = `calc(${BUBBLE_FRAME_PADDING}px + ${frame.y} * (100vh - ${frame.height}px))`;
      root.style.width = `max(0px, min(100vw, ${frame.width}px) - ${BUBBLE_FRAME_PADDING * 2}px)`;
      root.style.height = `max(0px, min(100vh, ${frame.height}px) - ${BUBBLE_FRAME_PADDING * 2}px)`;
    };
    const animateLayout = () => {
      if (dirty || !native?.visible || native.measurementRevision < revision) return;
      if (native.carrierPlan && native.hostRectPhysical) {
        const plan = native.carrierPlan;
        const planKey = JSON.stringify(plan);
        if (preparedCarrier === planKey) return;
        preparedCarrier = planKey;
        const host = native.hostRectPhysical;
        const scale = native.scaleFactor;
        const next = plan.rectPhysical;
        const x = next.width > host.width ? (host.x - next.x) / (next.width - host.width) : 0;
        const y = next.height > host.height ? (host.y - next.y) / (next.height - host.height) : 0;
        const snapshots = items().map((node) => ({ node, rect: node.getBoundingClientRect(), opacity: getComputedStyle(node).opacity }));
        cancelAnimations();
        running = false;
        // Install compensation before native growth. Fixed snapshots preserve
        // both surviving and already-exiting rows while the union is applied.
        for (const { node, rect, opacity } of snapshots) {
          node.style.position = "fixed";
          node.style.left = `calc(${rect.left}px + ${x} * (100vw - ${host.width / scale}px))`;
          node.style.top = `calc(${rect.top}px + ${y} * (100vh - ${host.height / scale}px))`;
          node.style.width = `${rect.width}px`;
          node.style.opacity = opacity;
        }
        void invoke<BubblePresentationState>("commit_bubble_carrier", { plan }).then(apply)
          .catch((error) => {
            if (!stopped && preparedCarrier === planKey) { preparedCarrier = ""; key = ""; }
            console.warn("bubble carrier preparation unavailable", error);
          });
        return;
      }
      if (!native.applied) return;
      preparedCarrier = "";
      const root = viewport();
      if (!root || !bubbleFrame(native)) return;
      const nextTarget = JSON.stringify([native.hostRectPhysical, native.targetRectPhysical, native.scaleFactor]);
      if (!pending) {
        // Desktop movement must not wait for a close animation.
        if (running && nextTarget !== plannedTarget && !native.retaining) {
          token += 1;
          cancelAnimations();
          running = false;
          root.style.overflow = "";
          root.style.paddingRight = "";
          const exiting = items().filter((item) => item.dataset.state === "exiting").map((item) => item.dataset.bubbleId!);
          exiting.forEach((id) => finishedExits.add(id));
          onExitComplete?.(exiting);
          void sendSize(false, token).catch((error) => console.warn("bubble bounds release unavailable", error));
          root.dataset.transitioning = "false";
        }
        setFrame(native);
        acknowledge();
        return;
      }
      pending = false;
      running = true;
      plannedTarget = nextTarget;
      const currentToken = token;
      const nodes = items();
      const before = new Map(nodes.map((node) => [node, { rect: node.getBoundingClientRect(), opacity: getComputedStyle(node).opacity }]));
      cancelAnimations();
      // FLIP rows may start outside the smaller final viewport. The retained
      // native carrier clips the transition; only settled content should scroll.
      const stack = document.querySelector<HTMLElement>("[data-bubble-stack]");
      const gutter = stack ? Math.max(0, root.getBoundingClientRect().width - stack.getBoundingClientRect().width) : 0;
      root.style.paddingRight = `${gutter}px`;
      root.style.overflow = "visible";
      setFrame(native);
      const active = nodes.filter((node) => node.dataset.state !== "exiting");
      const exiting = nodes.filter((node) => node.dataset.state === "exiting" && !finishedExits.has(node.dataset.bubbleId!));
      const gap = readAccessorySpacing() * 3.5;
      let offset = 0;
      for (const node of active) {
        node.style.position = "absolute";
        node.style.left = "0px";
        node.style.top = `${offset}px`;
        node.style.width = "100%";
        node.style.opacity = "1";
        offset += height(node) + gap;
      }
      if (stack) stack.style.height = `${Math.max(0, offset - gap)}px`;
      const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      const style = getComputedStyle(document.documentElement);
      const durationToken = style.getPropertyValue("--motion-duration-notification").trim();
      const duration = reduced ? 0 : (Number.parseFloat(durationToken) || 0) * (durationToken.endsWith("ms") ? 1 : 1000);
      const easing = style.getPropertyValue("--motion-ease-notification").trim() || "linear";
      const animate = (node: HTMLElement, frames: Keyframe[]) => {
        if (duration <= 0 || typeof node.animate !== "function") return;
        animations.add(node.animate(frames, { duration, easing, fill: "both" }));
      };
      for (const node of active) {
        const id = node.dataset.bubbleId!;
        const previous = before.get(node)!;
        const after = node.getBoundingClientRect();
        const existing = known.has(id);
        animate(node, [
          { transform: existing ? `translate(${previous.rect.x - after.x}px, ${previous.rect.y - after.y}px)` : "none", opacity: existing ? previous.opacity : 0 },
          { transform: "none", opacity: 1 },
        ]);
        known.add(id);
      }
      for (const node of exiting) {
        const previous = before.get(node)!;
        // Fixed exiting snapshots remain outside the smaller target scroll area.
        node.style.position = "fixed";
        node.style.left = `${previous.rect.left}px`;
        node.style.top = `${previous.rect.top}px`;
        node.style.width = `${previous.rect.width}px`;
        node.style.opacity = "0";
        animate(node, [{ opacity: previous.opacity }, { opacity: 0 }]);
      }
      const completed = Promise.allSettled([...animations].map((animation) => animation.finished));
      const finish = async () => {
        if (stopped || token !== currentToken) return;
        cancelAnimations();
        running = false;
        root.style.overflow = "";
        root.style.paddingRight = "";
        const ids = exiting.map((node) => node.dataset.bubbleId!);
        ids.forEach((id) => finishedExits.add(id));
        // Do not start a second transition when the completed snapshots unmount.
        key = JSON.stringify([active.map((node) => [node.dataset.bubbleId, height(node)]), []]);
        root.dataset.transitioning = "false";
        onExitComplete?.(ids);
        try {
          if (active.length) {
            const measurement = await sendSize(false, currentToken);
            if (measurement && !stopped && token === currentToken) revision = measurement.revision;
          }
          acknowledge();
        } catch (error) {
          if (!stopped && token === currentToken) key = "";
          console.warn("bubble bounds release unavailable", error);
        }
      };
      void completed.then(() => {
        if (stopped || token !== currentToken) return;
        // A mutation may precede its measurement by one frame. Let that frame
        // decide whether this animation still owns the queue before releasing it.
        if (dirty) completeAnimation = finish;
        else void finish();
      });
    };
    const apply = (state: BubblePresentationState) => {
      if (stopped || (native && (state.stateRevision <= native.stateRevision || state.layoutOwner < native.layoutOwner
        || (state.layoutOwner === native.layoutOwner && state.layoutRevision < native.layoutRevision)))) return;
      native = state;
      animateLayout();
    };
    const measure = async () => {
      const measuredDomRevision = domRevision;
      const nodes = items();
      const current = new Set(nodes);
      for (const element of observed) if (!current.has(element as HTMLElement)) { resize.unobserve(element); observed.delete(element); }
      for (const element of nodes) if (!observed.has(element)) { resize.observe(element); observed.add(element); }
      const active = nodes.filter((node) => node.dataset.state !== "exiting");
      active.forEach((node) => finishedExits.delete(node.dataset.bubbleId!));
      const exits = nodes.filter((node) => node.dataset.state === "exiting" && !finishedExits.has(node.dataset.bubbleId!));
      const nextKey = JSON.stringify([active.map((node) => [node.dataset.bubbleId, height(node)]), exits.map((node) => node.dataset.bubbleId)]);
      try { await visibility.setVisible(nodes.length > 0); } catch (error) { console.warn("bubble visibility unavailable", error); return; }
      if (stopped || measuredDomRevision !== domRevision) return;
      dirty = false;
      if (!nodes.length) {
        token += 1; key = ""; pending = false; running = false; revision = Number.POSITIVE_INFINITY;
        completeAnimation = null;
        cancelAnimations(); known.clear(); finishedExits.clear(); return;
      }
      if (nextKey === key) {
        const complete = completeAnimation;
        completeAnimation = null;
        if (complete) void complete();
        else animateLayout();
        acknowledge(); return;
      }
      completeAnimation = null;
      key = nextKey;
      const currentToken = ++token;
      pending = true;
      revision = Number.POSITIVE_INFINITY;
      if (viewport()) viewport()!.dataset.transitioning = "true";
      const gap = readAccessorySpacing() * 3.5;
      size = { width: BUBBLE_WINDOW_WIDTH, height: Math.ceil(active.reduce((sum, node) => sum + height(node), 0)
        + Math.max(0, active.length - 1) * gap + BUBBLE_FRAME_PADDING * 2) };
      try {
        const measurement = await sendSize(true, currentToken);
        if (!measurement || stopped || token !== currentToken) return;
        revision = measurement.revision;
        animateLayout();
      } catch (error) {
        if (!stopped && token === currentToken) { key = ""; pending = false; }
        console.warn("bubble measurement unavailable", error);
      }
    };
    function schedule() {
      dirty = true;
      domRevision += 1;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => { void measure(); });
    }
    const resize = new ResizeObserver(schedule);
    const mutation = new MutationObserver(schedule);
    mutation.observe(document.body, { subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ["data-state", "data-truncated"] });
    document.addEventListener("scroll", acknowledge, true);
    const timer = setInterval(() => { if (!key) schedule(); else acknowledge(); }, 1000);
    void listen<BubblePresentationState>(BUBBLE_LAYOUT_APPLIED_EVENT, (event) => apply(event.payload))
      .then(async (stop) => {
        if (stopped) { stop(); return; }
        unlisten = stop;
        apply(await invoke<BubblePresentationState>("get_bubble_presentation_state"));
      }).catch((error) => console.warn("bubble presentation unavailable", error));
    schedule();
    return () => {
      stopped = true; token += 1; cancelAnimationFrame(raf); clearInterval(timer); cancelAnimations();
      mutation.disconnect(); resize.disconnect(); unlisten?.(); visibility.dispose();
      document.removeEventListener("scroll", acknowledge, true);
    };
  }, [onExitComplete]);
}
