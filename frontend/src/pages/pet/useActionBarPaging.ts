import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { actionBarWheelDistance, createActionBarWheelGesture } from "./actionBarWheel";
import { getSettingsSnapshot, setSetting } from "@/lib/settings";

// Keep the latest user intent across hover unmounts, including an in-flight save.
// A single writer prevents older native writes from overtaking a newer page.
let preferredPage: number | undefined;
let pendingPage: number | undefined;
let saving: Promise<void> | undefined;

function rememberPage(page: number) {
  preferredPage = page;
  pendingPage = page;
  if (saving) return;
  saving = (async () => {
    while (pendingPage !== undefined) {
      const next = pendingPage;
      pendingPage = undefined;
      try { await setSetting("petActionBarPage", next); }
      catch (error) { console.warn("[pet] ActionBar page persistence failed", error); }
    }
  })().finally(() => {
    saving = undefined;
    if (pendingPage !== undefined) rememberPage(pendingPage);
  });
}

// Keep the exact offset through a hover remount. The durable setting remains a page number.
let lastPosition: { layout: string; offset: number } | undefined;

export function useActionBarPaging(strip: HTMLDivElement | null, vertical: boolean, pageSize: number, count: number, bar: RefObject<HTMLDivElement | null>) {
  const [state, setState] = useState({ currentPage: 0, canScrollPrev: false, canScrollNext: false });
  const initialPage = useRef(preferredPage ?? getSettingsSnapshot().petActionBarPage);

  useLayoutEffect(() => {
    const root = bar.current;
    if (!strip || !root) return;
    const axis = vertical ? "scrollTop" : "scrollLeft";
    const size = () => vertical ? strip.clientHeight : strip.clientWidth;
    const limit = () => Math.max(0, (vertical ? strip.scrollHeight : strip.scrollWidth) - size());
    const layout = () => `${vertical}:${pageSize}:${count}:${size()}`;
    let previousOffset = 0;
    let remainder = 0;

    const update = (remember: boolean) => {
      const offset = strip[axis];
      const max = limit();
      const extent = Math.max(1, size());
      const atEnd = offset >= max - 0.5;
      const page = atEnd ? Math.ceil(max / extent) : Math.round(offset / extent);
      setState({ currentPage: page, canScrollPrev: offset > 0.5, canScrollNext: !atEnd });
      if (remember && offset !== previousOffset) {
        lastPosition = { layout: layout(), offset };
        if (page !== initialPage.current) {
          initialPage.current = page;
          rememberPage(page);
        }
      }
      previousOffset = offset;
    };
    const restore = () => {
      strip[axis] = lastPosition?.layout === layout() ? lastPosition.offset : Math.min(initialPage.current * size(), limit());
      update(false);
    };
    const gesture = createActionBarWheelGesture();
    const lineHeight = Number.parseFloat(getComputedStyle(strip).lineHeight) || 16;
    const wheel = (event: WheelEvent) => {
      const delta = actionBarWheelDistance(event, lineHeight, size());
      if (!delta || !size()) return;
      event.preventDefault();
      const action = gesture(event, event.timeStamp, window.devicePixelRatio || 1);
      if (action === "ignore") return;
      if (action === "page") {
        remainder = 0;
        const offset = strip[axis];
        const extent = size();
        const page = delta > 0 ? Math.floor((offset + 0.5) / extent) + 1 : Math.ceil((offset - 0.5) / extent) - 1;
        const target = Math.max(0, Math.min(page * extent, limit()));
        strip.scrollTo({
          [vertical ? "top" : "left"]: target,
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
        });
        return;
      }
      const target = Math.max(0, Math.min(strip[axis] + delta + remainder, limit()));
      strip[axis] = target;
      remainder = target - strip[axis];
    };
    const onScroll = () => update(true);
    restore();
    const resize = new ResizeObserver(restore);
    resize.observe(strip);
    strip.addEventListener("scroll", onScroll);
    root.addEventListener("wheel", wheel, { passive: false });
    return () => {
      resize.disconnect();
      strip.removeEventListener("scroll", onScroll);
      root.removeEventListener("wheel", wheel);
    };
  }, [strip, vertical, pageSize, count, bar]);

  function scrollByPage(direction: -1 | 1) {
    if (!strip) return;
    strip.scrollBy(vertical ? { top: direction * strip.clientHeight } : { left: direction * strip.clientWidth });
  }

  return { ...state, scrollByPage };
}
