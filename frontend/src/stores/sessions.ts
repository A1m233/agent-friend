/**
 * 历史会话列表 store（design §4.3/§4.4）。store = 本窗口视图缓存，bridge 为真相源。
 */

import { create } from "zustand";
import { sessionsApi } from "@/services";
import type { SessionSummary } from "@/types/meta";

interface SessionsState {
  list: SessionSummary[];
  loading: boolean;
  /** 从 bridge 重新拉取会话列表（新建/切换后调用刷新）。 */
  refresh: () => Promise<void>;
}

export const useSessionsStore = create<SessionsState>((set) => ({
  list: [],
  loading: false,
  async refresh() {
    set({ loading: true });
    try {
      const list = await sessionsApi.list();
      set({ list, loading: false });
    } catch {
      // 列表拉取失败不弹技术错误，保留旧列表即可。
      set({ loading: false });
    }
  },
}));
