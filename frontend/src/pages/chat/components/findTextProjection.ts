/** Logical text for both inert Markdown HTML and mounted TDesign Markdown. */
export function projectFindText(root: Node) {
  let text = "";
  const chunks: Array<{ start: number; node: Text }> = [];
  function walk(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      chunks.push({ start: text.length, node: node as Text });
      text += node.textContent;
      return;
    }
    if (node instanceof Element) {
      if (node.matches("script,style,button,[hidden],[aria-hidden=true]")) return;
      if (node.matches("t-chat-md-code")) {
        const code = node.shadowRoot?.querySelector("pre code");
        if (code) walk(code);
        return;
      }
      if (node.matches("br")) { text += "\n"; return; }
    }
    node.childNodes.forEach(walk);
    if (node instanceof Element && node.matches("p,li,blockquote,pre,h1,h2,h3,h4,h5,h6,tr,td,th")) {
      text += "\n";
    }
  }
  walk(root);
  return { text, chunks };
}
