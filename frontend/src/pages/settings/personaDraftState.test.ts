import { describe, expect, it } from "vitest";
import { buildPersonaDiff } from "./personaDraftState";

describe("persona diff", () => {
  it("keeps unchanged lines and marks replacements", () => {
    expect(buildPersonaDiff("A\nB\nC", "A\nD\nC")).toEqual([
      { kind: "unchanged", text: "A" },
      { kind: "removed", text: "B" },
      { kind: "added", text: "D" },
      { kind: "unchanged", text: "C" },
    ]);
  });
});
