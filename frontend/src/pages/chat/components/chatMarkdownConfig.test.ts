import { describe, expect, it } from "vitest";

import { CHAT_MARKDOWN_CONTENT_PROPS } from "./chatMarkdownConfig";

describe("CHAT_MARKDOWN_CONTENT_PROPS", () => {
  it("disables bare URL autolinking in the chat markdown renderer", () => {
    expect(
      CHAT_MARKDOWN_CONTENT_PROPS.markdown.options.engine.syntax.autoLink,
    ).toBe(false);
  });
});
