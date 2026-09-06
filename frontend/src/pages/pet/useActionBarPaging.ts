import { useLayoutEffect, useRef, useState } from "react";
import type { CarouselApi } from "@/components/ui";
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

/** Only user selection changes are saved; initialization and layout clamps are not. */
export function useActionBarPaging(api: CarouselApi, enabled: boolean, layoutKey: string) {
  const [startIndex] = useState(() => preferredPage ?? getSettingsSnapshot().petActionBarPage);
  const preference = useRef(startIndex);
  const [state, setState] = useState({ currentPage: startIndex, canScrollPrev: false, canScrollNext: false });

  useLayoutEffect(() => {
    if (!enabled || !api) {
      setState({ currentPage: 0, canScrollPrev: false, canScrollNext: false });
      return;
    }
    let restoring = false;
    const update = () => setState({
      currentPage: api.selectedScrollSnap(),
      canScrollPrev: api.canScrollPrev(),
      canScrollNext: api.canScrollNext(),
    });
    const restore = () => {
      const page = Math.max(0, Math.min(preference.current, api.scrollSnapList().length - 1));
      restoring = true;
      try { api.scrollTo(page, true); } finally { restoring = false; }
      update();
    };
    const select = () => {
      update();
      if (restoring) return;
      const page = api.selectedScrollSnap();
      if (page === preference.current) return;
      preference.current = page;
      rememberPage(page);
    };
    api.on("select", select);
    api.on("reInit", restore);
    restore();
    return () => { api.off("select", select); api.off("reInit", restore); };
  }, [api, enabled, layoutKey]);

  return { ...state, startIndex };
}
