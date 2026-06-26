import { describe, expect, it } from "vitest";
import { formatRelativeMessageTime } from "./formatRelativeMessageTime";

describe("formatRelativeMessageTime", () => {
  const now = new Date(2026, 5, 26, 12, 0);

  it("今天只显示时间", () => {
    expect(formatRelativeMessageTime(new Date(2026, 5, 26, 9, 5), now)).toBe("09:05");
  });

  it("昨天显示昨天和时间", () => {
    expect(formatRelativeMessageTime(new Date(2026, 5, 25, 23, 10), now)).toBe("昨天 23:10");
  });

  it("近 7 天显示星期和时间", () => {
    expect(formatRelativeMessageTime(new Date(2026, 5, 22, 8, 30), now)).toBe("星期一 08:30");
  });

  it("今年更早显示月日和时间", () => {
    expect(formatRelativeMessageTime(new Date(2026, 5, 1, 8, 5), now)).toBe("6月1日 08:05");
  });

  it("跨年显示年份、月日和时间", () => {
    expect(formatRelativeMessageTime(new Date(2025, 11, 31, 22, 15), now)).toBe(
      "2025年12月31日 22:15",
    );
  });

  it("非法时间返回空字符串", () => {
    expect(formatRelativeMessageTime("not-a-date", now)).toBe("");
  });
});
