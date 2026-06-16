import { describe, expect, it } from "vitest";
import { derivePageState } from "./actionBarPaging";

describe("derivePageState", () => {
  describe("buttonCount ≤ pageSize → 不启用 carousel", () => {
    it("少于一页（3/6）：无箭头、单页", () => {
      expect(
        derivePageState({ buttonCount: 3, pageSize: 6, currentPage: 0 })
      ).toEqual({
        needsCarousel: false,
        showPrev: false,
        showNext: false,
        totalPages: 1,
      });
    });

    it("正好一页（6/6）：无箭头、单页", () => {
      expect(
        derivePageState({ buttonCount: 6, pageSize: 6, currentPage: 0 })
      ).toEqual({
        needsCarousel: false,
        showPrev: false,
        showNext: false,
        totalPages: 1,
      });
    });

    it("dev 默认（5/6）：无箭头", () => {
      const r = derivePageState({ buttonCount: 5, pageSize: 6, currentPage: 0 });
      expect(r.needsCarousel).toBe(false);
      expect(r.showPrev).toBe(false);
      expect(r.showNext).toBe(false);
    });
  });

  describe("buttonCount > pageSize → 启用 carousel", () => {
    it("8/6 首页（page=0）：只渲染右箭头", () => {
      expect(
        derivePageState({ buttonCount: 8, pageSize: 6, currentPage: 0 })
      ).toEqual({
        needsCarousel: true,
        showPrev: false,
        showNext: true,
        totalPages: 2,
      });
    });

    it("8/6 末页（page=1）：只渲染左箭头", () => {
      expect(
        derivePageState({ buttonCount: 8, pageSize: 6, currentPage: 1 })
      ).toEqual({
        needsCarousel: true,
        showPrev: true,
        showNext: false,
        totalPages: 2,
      });
    });

    it("13/6 中间页（page=1）：两侧箭头都渲染", () => {
      expect(
        derivePageState({ buttonCount: 13, pageSize: 6, currentPage: 1 })
      ).toEqual({
        needsCarousel: true,
        showPrev: true,
        showNext: true,
        totalPages: 3,
      });
    });

    it("13/6 首页（page=0）：只右箭头", () => {
      const r = derivePageState({
        buttonCount: 13,
        pageSize: 6,
        currentPage: 0,
      });
      expect(r.showPrev).toBe(false);
      expect(r.showNext).toBe(true);
      expect(r.totalPages).toBe(3);
    });

    it("13/6 末页（page=2）：只左箭头", () => {
      const r = derivePageState({
        buttonCount: 13,
        pageSize: 6,
        currentPage: 2,
      });
      expect(r.showPrev).toBe(true);
      expect(r.showNext).toBe(false);
      expect(r.totalPages).toBe(3);
    });

    it("12/6 整除（恰好 2 页）首页：只右箭头", () => {
      expect(
        derivePageState({ buttonCount: 12, pageSize: 6, currentPage: 0 })
      ).toEqual({
        needsCarousel: true,
        showPrev: false,
        showNext: true,
        totalPages: 2,
      });
    });
  });

  describe("totalPages 计算（向上取整）", () => {
    it("7/6 → 2 页", () => {
      expect(
        derivePageState({ buttonCount: 7, pageSize: 6, currentPage: 0 })
          .totalPages
      ).toBe(2);
    });
    it("12/6 → 2 页", () => {
      expect(
        derivePageState({ buttonCount: 12, pageSize: 6, currentPage: 0 })
          .totalPages
      ).toBe(2);
    });
    it("13/6 → 3 页", () => {
      expect(
        derivePageState({ buttonCount: 13, pageSize: 6, currentPage: 0 })
          .totalPages
      ).toBe(3);
    });
  });
});
