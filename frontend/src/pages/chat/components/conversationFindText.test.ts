// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { findOccurrences, prepareFindBlocks } from "./conversationFindText";

describe("conversation find", () => {
  it("counts rendered occurrences in block order, including formatted and repeated text", async () => {
    const blocks = await prepareFindBlocks([
      { role: "assistant", anchor: "process", text: "[**Need**le](https://hidden.example/needle) and NEEDLE" },
      { role: "user", anchor: "reply", text: "needle **literal**" },
    ], new AbortController().signal);
    expect(findOccurrences(blocks, "needle")).toEqual([
      { anchor: "process", occurrence: 0 }, { anchor: "process", occurrence: 1 },
      { anchor: "reply", occurrence: 0 },
    ]);
    expect(findOccurrences(blocks, "hidden.example")).toEqual([]);
    expect(findOccurrences(blocks, "**literal**")).toEqual([{ anchor: "reply", occurrence: 0 }]);
  });
});
