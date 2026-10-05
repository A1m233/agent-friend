/** Inert hrefs preserve a renderer's original target without allowing WebView navigation. */
const LINK_PREFIX = "#agent-friend-link=";

export type LinkContext = { kind: "session"; sessionId: string | null } | { kind: "persona" };
export type LinkTarget =
  | { kind: "external"; value: string }
  | { kind: "local"; value: string }
  | { kind: "anchor"; value: string }
  | { kind: "unavailable"; value: string; reason: string };

export function managedLinkHref(value: string): string {
  return LINK_PREFIX + encodeURIComponent(value);
}

export function originalLinkHref(value: string): string {
  if (!value.startsWith(LINK_PREFIX)) return value;
  try { return decodeURIComponent(value.slice(LINK_PREFIX.length)); }
  catch { return ""; }
}

export function classifyLink(href: string): LinkTarget {
  const value = originalLinkHref(href).trim();
  const unavailable = (reason: string): LinkTarget => ({ kind: "unavailable", value, reason });
  if (!value || Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return unavailable("链接地址为空或包含无效字符");
  if (value.startsWith("#")) return { kind: "anchor", value };
  if (value.startsWith("//")) return unavailable("无法确定这是网址还是共享目录，请使用完整地址");
  if (/^[a-z]:[\\/]/i.test(value) || value.startsWith("\\\\")) return { kind: "local", value };
  const protocol = value.match(/^([a-z][a-z\d+.-]*):/i)?.[1]?.toLowerCase();
  if (protocol === "file") return { kind: "local", value };
  if (protocol && ["http", "https", "mailto", "tel"].includes(protocol)) {
    try { return { kind: "external", value: new URL(value).href }; }
    catch { return unavailable("链接地址格式无效"); }
  }
  if (protocol) return unavailable("当前不支持此链接类型");
  if (value.startsWith("~")) return unavailable("请使用完整文件路径，不能确定此路径的用户目录");
  return { kind: "local", value };
}

export function markdownLinkUrl(url: string, type: string): string {
  if (type !== "link" && type !== "autolink") return url;
  // Cherry passes Markdown destination delimiters through (sometimes HTML-escaped).
  // These are syntax, not part of the file name; leave percent escapes untouched.
  const value = originalLinkHref(url).replace(/^(?:<|&#60;|&lt;)([\s\S]*)(?:>|&#62;|&gt;)$/, "$1");
  return managedLinkHref(value);
}

export function markdownLinkAttributes(_text: string, href: string): string {
  // Reference definitions are temporarily hashed by Cherry, then restored after
  // attribute rendering. Classification must wait until the real href is exposed.
  if (href.startsWith("cherry-inner://")) return "";
  const target = classifyLink(href);
  if (target.kind !== "unavailable") return "";
  const reason = target.reason.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
  return `aria-disabled="true" title="${reason}" style="color:var(--muted);cursor:help"`;
}

interface MarkdownNode {
  type: string; url?: string; title?: string | null; identifier?: string;
  referenceType?: string; label?: string; children?: MarkdownNode[];
}

/** Preserve destinations before mdast-to-hast encodes Unicode and literal percent signs. */
export function remarkManagedLinks() {
  return (tree: MarkdownNode) => {
    const definitions = new Map<string, MarkdownNode>();
    const collect = (node: MarkdownNode) => {
      if (node.type === "definition" && node.identifier && !definitions.has(node.identifier.toLowerCase())) {
        definitions.set(node.identifier.toLowerCase(), node);
      }
      node.children?.forEach(collect);
    };
    collect(tree);
    const visit = (node: MarkdownNode) => {
      if (node.type === "linkReference" && node.identifier) {
        const definition = definitions.get(node.identifier.toLowerCase());
        if (definition?.url !== undefined) {
          node.type = "link";
          node.url = definition.url;
          node.title = definition.title;
          delete node.identifier;
          delete node.referenceType;
          delete node.label;
        }
      }
      if (node.type === "link" && node.url !== undefined) {
        node.url = managedLinkHref(node.url);
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}
