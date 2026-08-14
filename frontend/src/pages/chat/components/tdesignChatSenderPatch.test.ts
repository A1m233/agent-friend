import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

const projectRequire = createRequire(import.meta.url);
const reactChatSenderEntry = projectRequire.resolve(
  "@tdesign-react/chat/es/chat-sender/index.js",
);
const reactChatRequire = createRequire(reactChatSenderEntry);

function readPatchedChatSenderFile(relativePath: string) {
  const path = reactChatRequire.resolve(`tdesign-web-components/lib/chat-sender/${relativePath}`);
  return readFileSync(path, "utf8");
}

describe("tdesign ChatSender patch", () => {
  it("renders one rotating loading icon inside the built-in button and observes state changes", () => {
    const source = readPatchedChatSenderFile("chat-sender.js");
    const typeDeclaration = readPatchedChatSenderFile("type.d.ts");

    expect(source).toContain("_this.props.sendBtnLoading ?");
    expect(source).toContain('Component.h("t-icon-loading", {');
    expect(source).toContain('t-chat-sender-loading-spin');
    expect(source).toContain('"css", [styles, sendButtonLoadingStyles]');
    expect(source).toContain('__button__loading');
    expect(source).not.toContain('t-chat-circle-pending');
    expect(source).not.toContain('Component.h("t-chat-loading", {');
    expect(source).toContain("disabled: disabled || _this.props.sendBtnLoading");
    expect(source).not.toContain("loading: _this.props.sendBtnLoading");
    expect(source).toContain(
      "if (props.sendBtnLoading !== oldProps.sendBtnLoading) return true;",
    );
    expect(typeDeclaration).toContain("sendBtnLoading?: boolean;");
  });
});
