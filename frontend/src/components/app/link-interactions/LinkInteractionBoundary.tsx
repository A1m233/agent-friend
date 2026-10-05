import { useLayoutEffect, useRef, useState, type ReactNode, type MouseEvent } from "react";
import { toast } from "sonner";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuLabel, ContextMenuSeparator, ContextMenuTrigger } from "@/components/ui";
import { resolveProductContextMenuPolicy } from "@/lib/contextMenu";
import { activateLink, copyLink, linkActionLabel, resolveLink, type ResolvedLink } from "@/lib/links/actions";
import { originalLinkHref, type LinkContext } from "@/lib/links/targets";
import { isTauri } from "@/utils/tauri";

interface Hit { node: Element; href: string; image: boolean }

function currentHit(hit: Hit): boolean {
  if (!hit.node.isConnected) return false;
  if (hit.node instanceof HTMLAnchorElement) return hit.node.getAttribute("href") === hit.href;
  return (hit.node as Element & { props?: { item?: { url?: string } } }).props?.item?.url === hit.href;
}

/** All renderer-specific event adaptation stays here, not in individual menu implementations. */
export function linkAtEvent(event: Event): Hit | null {
  const path = event.composedPath();
  if (path.some(item => item instanceof Element && (item.hasAttribute("data-link-ignore") || item.classList.contains("t-filecard-remove")))) return null;
  for (const item of event.composedPath()) {
    if (!(item instanceof Element)) continue;
    if (item.hasAttribute("data-link-ignore")) return null;
    if (item instanceof HTMLAnchorElement) return { node: item, href: item.getAttribute("href") ?? "", image: false };
    if (item.tagName === "T-FILECARD") {
      const card = item as Element & { props?: { item?: { url?: string; fileType?: string } } };
      const value = card.props?.item;
      if (value?.url) return { node: item, href: value.url, image: value.fileType === "image" };
    }
  }
  return null;
}

function feedback(error: unknown) {
  toast.error(error instanceof Error ? error.message : "链接操作失败，请稍后重试");
}

interface MenuTarget { hit: Hit; resolved?: ResolvedLink; selectedText: string; sourceKey: string; keyboard: boolean }

export function LinkInteractionBoundary({ context, children }: { context: LinkContext; children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const actionGeneration = useRef(0);
  const contextKey = JSON.stringify(context);
  const activeContext = useRef(contextKey);
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    activeContext.current = contextKey;
    generation.current++;
    setOpen(false);
    setMenu(null);
    actionGeneration.current++;
    return () => { generation.current++; actionGeneration.current++; };
  }, [contextKey]);

  const menuHit = menu?.hit;
  useLayoutEffect(() => {
    if (!open || !menuHit) return;
    const check = () => {
      if (!currentHit(menuHit)) {
        generation.current++;
        setOpen(false);
        setMenu(null);
      }
    };
    const observer = new MutationObserver(check);
    observer.observe(document.body, { subtree: true, childList: true });
    observer.observe(menuHit.node, { attributes: true, attributeFilter: ["href"] });
    const contentRoot = menuHit.node.getRootNode();
    if (contentRoot instanceof ShadowRoot) observer.observe(contentRoot, { subtree: true, childList: true });
    return () => observer.disconnect();
  }, [open, menuHit]);

  const resolve = async (hit: Hit): Promise<ResolvedLink> => {
    let result: ResolvedLink;
    try { result = await resolveLink(hit.href, context); }
    catch (error) { result = { kind: "unavailable", value: originalLinkHref(hit.href), reason: error instanceof Error ? error.message : "无法解析链接" }; }
    if (hit.node instanceof HTMLAnchorElement && hit.node.getAttribute("href") === hit.href) {
      hit.node.title = result.kind === "unavailable" ? result.reason ?? "链接不可用" : result.value;
      if (result.kind === "unavailable") {
        hit.node.setAttribute("aria-disabled", "true");
        hit.node.style.color = "var(--muted)";
        hit.node.style.cursor = "help";
      } else {
        hit.node.removeAttribute("aria-disabled");
        hit.node.style.removeProperty("color");
        hit.node.style.removeProperty("cursor");
      }
    }
    return result;
  };
  const valid = (hit: Hit, version: number) => version === generation.current && currentHit(hit);

  const activate = async (hit: Hit) => {
    const version = ++actionGeneration.current;
    const target = await resolve(hit);
    if (version !== actionGeneration.current || !currentHit(hit) || !rootRef.current) return;
    const contentRoot = hit.node.getRootNode();
    try { await activateLink(target, contentRoot instanceof ShadowRoot ? contentRoot : rootRef.current); } catch (error) { feedback(error); }
  };

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.type === "auxclick" && event.button !== 1) return;
    const hit = linkAtEvent(event.nativeEvent);
    if (!hit || hit.image || event.defaultPrevented) return;
    event.preventDefault();
    event.stopPropagation();
    void activate(hit);
  };

  return <ContextMenu open={open} onOpenChange={(next) => {
    setOpen(next);
    // Closing invalidates pending resolution without clearing the selected action synchronously.
    if (!next) generation.current++;
  }}>
    <ContextMenuTrigger asChild data-context-menu-policy={undefined}>
      <div ref={rootRef} className="contents" onClickCapture={onClick} onAuxClickCapture={onClick}
        onContextMenu={(event) => { if (!linkAtEvent(event.nativeEvent)) event.preventDefault(); }}
        onKeyDownCapture={(event) => {
          if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
          const hit = linkAtEvent(event.nativeEvent);
          if (!hit) return;
          event.preventDefault();
          event.stopPropagation();
          const rect = hit.node.getBoundingClientRect();
          hit.node.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, composed: true, cancelable: true, clientX: rect.left, clientY: rect.bottom }));
        }}
        onContextMenuCapture={(event) => {
          const hit = linkAtEvent(event.nativeEvent);
          if (!hit) {
            const policy = resolveProductContextMenuPolicy(event.nativeEvent);
            // Existing nested product menus own their event; prevent only our outer
            // trigger's default in the bubble handler, after the child has handled it.
            if (policy === "custom") return;
            if (policy !== "native" && isTauri()) event.preventDefault();
            event.stopPropagation();
            return;
          }
          const version = ++generation.current;
          const selectedText = window.getSelection()?.toString() ?? "";
          const snapshot = { hit, selectedText, sourceKey: contextKey, keyboard: event.button === 0 };
          setMenu(snapshot);
          void resolve(hit).then(resolved => {
            if (valid(hit, version)) setMenu({ ...snapshot, resolved });
          });
        }}>
        {children}
      </div>
    </ContextMenuTrigger>
    {open && menu && <ContextMenuContent onCloseAutoFocus={(event) => {
      event.preventDefault();
      if (menu.keyboard && menu.sourceKey === activeContext.current && currentHit(menu.hit) && menu.hit.node instanceof HTMLElement) {
        menu.hit.node.focus({ preventScroll: true });
      }
    }}>
      {!menu.resolved ? <ContextMenuLabel>正在解析链接…</ContextMenuLabel>
        : menu.resolved.kind === "unavailable" ? <ContextMenuLabel>{menu.resolved.reason}</ContextMenuLabel>
        : <ContextMenuItem onSelect={() => { void activate(menu.hit); }}>{linkActionLabel(menu.resolved)}</ContextMenuItem>}
      <ContextMenuItem onSelect={() => { void copyLink(menu.resolved?.value ?? originalLinkHref(menu.hit.href)).catch(feedback); }}>
        {menu.resolved?.kind === "file" || menu.resolved?.kind === "directory" ? "复制完整路径" : "复制链接"}
      </ContextMenuItem>
      {menu.selectedText && <><ContextMenuSeparator /><ContextMenuItem onSelect={() => { void copyLink(menu.selectedText).catch(feedback); }}>复制选中文本</ContextMenuItem></>}
    </ContextMenuContent>}
  </ContextMenu>;
}
