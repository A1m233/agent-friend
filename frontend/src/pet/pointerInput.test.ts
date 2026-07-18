import { describe, expect, it } from "vitest";
import { isPrimaryPointerButton } from "./pointerInput";

describe("isPrimaryPointerButton", () => {
  it.each([
    ["鼠标左键", { pointerType: "mouse", button: 0, isPrimary: true }],
    ["主触点", { pointerType: "touch", button: 0, isPrimary: true }],
    ["触控笔笔尖", { pointerType: "pen", button: 0, isPrimary: true }],
  ])("允许%s", (_label, event) => {
    expect(isPrimaryPointerButton(event)).toBe(true);
  });

  it.each([
    ["鼠标中键", { pointerType: "mouse", button: 1, isPrimary: true }],
    ["鼠标右键", { pointerType: "mouse", button: 2, isPrimary: true }],
    ["触控笔侧键", { pointerType: "pen", button: 2, isPrimary: true }],
    ["非主触点", { pointerType: "touch", button: 0, isPrimary: false }],
  ])("拒绝%s", (_label, event) => {
    expect(isPrimaryPointerButton(event)).toBe(false);
  });

  it.each([null, undefined, {}, { button: "0" }])("拒绝无效事件 %#", (event) => {
    expect(isPrimaryPointerButton(event)).toBe(false);
  });
});
