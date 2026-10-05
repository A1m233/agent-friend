import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { http } from "@/services/http";
import { BRIDGE_BASE_URL } from "@/constants";
import { isTauri } from "@/utils/tauri";
import { classifyLink, type LinkContext } from "./targets";

export interface ResolvedLink {
  kind: "file" | "directory" | "external" | "anchor" | "unavailable";
  value: string;
  reason?: string;
}

/** Only the exact bridge origin and attachment route are attachment identities. */
export function attachmentReference(value: string): { assetId?: string; draftId?: string } | null {
  try {
    const base = new URL(BRIDGE_BASE_URL, "http://localhost");
    const url = new URL(value, base);
    if (url.origin !== base.origin || url.search || url.hash) return null;
    const match = url.pathname.match(/^\/v1\/attachments\/(assets|drafts)\/([^/]+)\/content$/);
    if (!match) return null;
    return match[1] === "assets" ? { assetId: decodeURIComponent(match[2]) } : { draftId: decodeURIComponent(match[2]) };
  } catch { return null; }
}

export async function resolveLink(href: string, context: LinkContext): Promise<ResolvedLink> {
  const target = classifyLink(href);
  const attachment = attachmentReference(target.value);
  if (!attachment && target.kind !== "local") return target;
  if (!isTauri()) return { kind: "unavailable", value: target.value, reason: "请在桌面端定位本地文件" };
  const resolved = await http.post<unknown, ResolvedLink>("/v1/link-targets/resolve", {
    target: attachment ? undefined : target.value,
    ...attachment,
    context: context.kind,
    sessionId: context.kind === "session" ? context.sessionId : undefined,
  });
  return resolved;
}

export async function activateLink(target: ResolvedLink, root: ParentNode): Promise<void> {
  if (target.kind === "unavailable") throw new Error(target.reason || "链接当前不可用");
  if (target.kind === "external") { await openUrl(target.value); return; }
  if (target.kind === "anchor") {
    let id: string;
    try { id = decodeURIComponent(target.value.slice(1)); } catch { throw new Error("链接锚点格式无效"); }
    const search = (node: ParentNode): Element | null => {
      for (const element of node.querySelectorAll("*")) {
        if (element.id === id) return element;
        const child = element.shadowRoot && search(element.shadowRoot);
        if (child) return child;
      }
      return null;
    };
    const element = id && search(root);
    if (!element) throw new Error("没有找到此链接指向的内容");
    element.scrollIntoView({ block: "nearest" });
    return;
  }
  await invoke("reveal_link_target", { path: target.value, expectedKind: target.kind });
}

export function linkActionLabel(target: ResolvedLink): string {
  if (target.kind === "file") return "打开所在文件夹";
  if (target.kind === "directory") return "打开文件夹";
  if (target.kind === "anchor") return "跳转到内容";
  return "打开链接";
}

export async function copyLink(value: string): Promise<void> {
  if (!value) throw new Error("没有可复制的地址");
  try { await navigator.clipboard.writeText(value); }
  catch { throw new Error("复制失败，请稍后重试"); }
}
