import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";
import { Toaster } from "@/components/ui";
import { isTauri } from "@/utils/tauri";
import { usePetBubbleStore } from "@/stores/petBubble";
import {
  BUBBLE_FRAME_PADDING,
  BUBBLE_VISIBLE_TOASTS,
  calculateBubbleStackWindowSize,
  parseCssPixels,
} from "./bubbleWindowSize";

/**
 * 015 + 016 + 070 · 独立 bubble webview 内的桌宠短气泡栈。
 *
 * Sonner 只负责 toast 生命周期、堆叠和逐条关闭；Zustand store 仍是 push 去重、Reminder
 * 保留和普通主动轮替换语义的真相源。透明 Tauri window 不会自动跟随 fixed toast 子树
 * 变高，因此本组件还会测量 Sonner 的目标布局并调用 `set_bubble_size`，同时在四周为阴影
 * 和关闭按钮预留透明边距。
 */
const TRUNCATE_LEN = 120;
const TOASTER_ID = "pet-bubble";

export function PetBubble() {
  const items = usePetBubbleStore((state) => state.items);
  const expand = usePetBubbleStore((state) => state.expand);
  const dismiss = usePetBubbleStore((state) => state.dismiss);
  const renderedIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    const nextIds = new Set(items.map((item) => item.id));
    for (const id of renderedIds.current) {
      if (!nextIds.has(id)) toast.dismiss(id);
    }

    for (const item of items) {
      const truncated = item.text.length > TRUNCATE_LEN && !item.expanded;
      const displayText = truncated
        ? `${item.text.slice(0, TRUNCATE_LEN)}…`
        : item.text;
      toast(displayText, {
        id: item.id,
        toasterId: TOASTER_ID,
        duration: Infinity,
        onDismiss: () => dismiss(item.id),
        action: truncated
          ? {
              label: "展开",
              onClick: (event) => {
                event.preventDefault();
                expand(item.id);
              },
            }
          : undefined,
      });
    }
    renderedIds.current = nextIds;
  }, [dismiss, expand, items]);

  useEffect(() => {
    if (!isTauri()) return;

    let raf = 0;
    const observed = new Set<Element>();
    const resizeObserver = new ResizeObserver(() => scheduleMeasure());

    const refreshObservedToasts = () => {
      const current = new Set(document.querySelectorAll("[data-sonner-toast]"));
      for (const element of observed) {
        if (!current.has(element)) {
          resizeObserver.unobserve(element);
          observed.delete(element);
        }
      }
      for (const element of current) {
        if (!observed.has(element)) {
          resizeObserver.observe(element);
          observed.add(element);
        }
      }
    };

    const measure = () => {
      refreshObservedToasts();
      const layout = Array.from(
        document.querySelectorAll<HTMLElement>(
          '[data-sonner-toast][data-visible="true"]:not([data-removed="true"])',
        ),
        (element) => ({
          height: element.offsetHeight,
          offset: parseCssPixels(element.style.getPropertyValue("--offset")),
        }),
      );
      if (layout.length === 0) return;

      const size = calculateBubbleStackWindowSize(layout);
      void invoke("set_bubble_size", {
        width: size.width,
        height: size.height,
      }).catch((error: unknown) => {
        console.warn("set_bubble_size invoke failed:", error);
      });
    };

    function scheduleMeasure() {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    }

    const mutationObserver = new MutationObserver(() => {
      refreshObservedToasts();
      scheduleMeasure();
    });
    mutationObserver.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["style", "data-mounted", "data-removed", "data-visible"],
    });
    scheduleMeasure();

    return () => {
      cancelAnimationFrame(raf);
      mutationObserver.disconnect();
      resizeObserver.disconnect();
    };
  }, []);

  return (
    <Toaster
      id={TOASTER_ID}
      position="top-center"
      expand
      visibleToasts={BUBBLE_VISIBLE_TOASTS}
      closeButton
      duration={Infinity}
      offset={BUBBLE_FRAME_PADDING}
      mobileOffset={BUBBLE_FRAME_PADDING}
      containerAriaLabel="桌宠提醒"
      toastOptions={{
        classNames: {
          toast: "items-start",
          title: "whitespace-pre-wrap break-words leading-snug",
          actionButton: "self-center",
        },
      }}
    />
  );
}
