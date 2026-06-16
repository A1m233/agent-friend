/**
 * 019 · ActionBar 分页判定纯函数。
 *
 * 抽离自 ActionBar 组件，便于在 node 环境下做单测覆盖（项目 vitest 环境为 node
 * + 未装 RTL/jsdom；TooltipButton 这种纯 JSX 拼装件按 dev-workflow 豁免单测，
 * 但分页判定有真断言价值，独立纯函数化测）。
 *
 * 行为：
 * - buttonCount ≤ pageSize：不启用 carousel，纯 flex 平铺，左右箭头都不渲染
 * - buttonCount > pageSize：启用 carousel，首页不渲染"上一页"，末页不渲染"下一页"
 *   （首末页箭头**不渲染**而非 disabled，沿 requirement.md R-4.2.3）
 */

export interface PageState {
  /** 按钮总数 > pageSize 时为 true，需启用 carousel；否则纯 flex 平铺 */
  needsCarousel: boolean;
  /** 是否渲染"上一页"箭头（首页或不需要 carousel 时为 false） */
  showPrev: boolean;
  /** 是否渲染"下一页"箭头（末页或不需要 carousel 时为 false） */
  showNext: boolean;
  /** 总页数（≤ pageSize 时为 1） */
  totalPages: number;
}

export function derivePageState(params: {
  buttonCount: number;
  pageSize: number;
  /** 当前页索引（0-based）；needsCarousel=false 时被忽略 */
  currentPage: number;
}): PageState {
  const { buttonCount, pageSize, currentPage } = params;
  if (buttonCount <= pageSize) {
    return {
      needsCarousel: false,
      showPrev: false,
      showNext: false,
      totalPages: 1,
    };
  }
  const totalPages = Math.ceil(buttonCount / pageSize);
  return {
    needsCarousel: true,
    showPrev: currentPage > 0,
    showNext: currentPage < totalPages - 1,
    totalPages,
  };
}
