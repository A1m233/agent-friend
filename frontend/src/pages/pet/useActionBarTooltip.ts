import { useEffect, useRef, useState } from "react";

/** Native scrolling closes Radix tooltips; a stationary pointer won't reopen the final icon. */
export function useActionBarTooltip(strip: HTMLDivElement | null) {
  const [openAction, setOpenAction] = useState<string | null>(null);
  const settling = useRef(false);

  useEffect(() => {
    if (!strip) return;
    let pointer: { x: number; y: number } | null = null;
    let timer: number | undefined;
    const cancel = () => {
      window.clearTimeout(timer);
      settling.current = false;
      pointer = null;
    };
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) pointer = { x: event.clientX, y: event.clientY };
    };
    const onPointerMove = (event: PointerEvent) => {
      if (pointer && event.pointerType === "mouse") pointer = { x: event.clientX, y: event.clientY };
    };
    const onScroll = () => {
      if (!pointer) return;
      settling.current = true;
      setOpenAction(null);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (!pointer) return;
        const hit = document.elementFromPoint(pointer.x, pointer.y)?.closest<HTMLElement>("[data-pet-action]");
        cancel();
        if (hit && strip.contains(hit)) setOpenAction(hit.dataset.petAction ?? null);
      }, 120);
    };
    strip.addEventListener("wheel", onWheel, { passive: true });
    strip.addEventListener("scroll", onScroll);
    strip.addEventListener("pointermove", onPointerMove);
    strip.addEventListener("pointerleave", cancel);
    strip.addEventListener("pointerdown", cancel);
    window.addEventListener("keydown", cancel, true);
    window.addEventListener("blur", cancel);
    return () => {
      cancel();
      strip.removeEventListener("wheel", onWheel);
      strip.removeEventListener("scroll", onScroll);
      strip.removeEventListener("pointermove", onPointerMove);
      strip.removeEventListener("pointerleave", cancel);
      strip.removeEventListener("pointerdown", cancel);
      window.removeEventListener("keydown", cancel, true);
      window.removeEventListener("blur", cancel);
    };
  }, [strip]);

  return (action: string) => ({
    open: openAction === action,
    onOpenChange(open: boolean) {
      if (open && !settling.current) setOpenAction(action);
      if (!open) setOpenAction(current => current === action ? null : current);
    },
  });
}
