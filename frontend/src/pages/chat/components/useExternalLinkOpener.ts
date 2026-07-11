import { useCallback, type MouseEvent as ReactMouseEvent } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

const OPENABLE_EXTERNAL_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"]);

function findAnchorInComposedPath(event: ReactMouseEvent<HTMLElement>): HTMLAnchorElement | null {
  for (const target of event.nativeEvent.composedPath()) {
    if (target instanceof HTMLAnchorElement) return target;
  }
  if (event.target instanceof Element) return event.target.closest("a");
  return null;
}

function hasAbsoluteProtocol(rawHref: string): boolean {
  return /^[a-zA-Z][a-zA-Z\d+.-]*:/.test(rawHref);
}

function stripMarkdownAutoLinkArtifacts(rawHref: string): string {
  let href = rawHref.trim();
  for (const marker of ["**", "__"]) {
    while (href.endsWith(marker)) {
      href = href.slice(0, -marker.length).trimEnd();
    }
  }
  while (href.endsWith("*") || href.endsWith("_")) {
    href = href.slice(0, -1).trimEnd();
  }
  return href;
}

export function toOpenableExternalUrl(href: string, textContent?: string | null): string | null {
  const candidates = [textContent ?? "", href]
    .map(stripMarkdownAutoLinkArtifacts)
    .filter((candidate, index, all) => candidate && all.indexOf(candidate) === index);

  for (const rawHref of candidates) {
    if (rawHref.startsWith("#")) continue;
    if (!hasAbsoluteProtocol(rawHref)) continue;

    try {
      const url = new URL(rawHref);
      if (!OPENABLE_EXTERNAL_PROTOCOLS.has(url.protocol)) continue;
      return url.href;
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

export function useExternalLinkOpener() {
  return useCallback((event: ReactMouseEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;

    const anchor = findAnchorInComposedPath(event);
    if (!anchor) return;

    event.preventDefault();
    const url = toOpenableExternalUrl(anchor.getAttribute("href") ?? "", anchor.textContent);
    if (!url) {
      console.warn("[chat][external-link] blocked unsupported link", {
        href: anchor.getAttribute("href"),
      });
      return;
    }

    void openUrl(url).catch((error: unknown) => {
      console.warn("[chat][external-link] failed to open link", { url, error });
    });
  }, []);
}
