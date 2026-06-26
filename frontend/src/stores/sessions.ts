/**
 * 历史会话列表 store（design §4.3/§4.4）。store = 本窗口视图缓存，bridge 为真相源。
 */

import { create } from "zustand";
import { sessionsApi } from "@/services";
import type { SessionSummary } from "@/types/meta";

interface SessionsState {
  list: SessionSummary[];
  loading: boolean;
  /** 至少成功拉取过一次列表；用于区分“空列表”和“还没拿到真相源”。 */
  loaded: boolean;
  /** 从 bridge 重新拉取会话列表（新建/切换后调用刷新）。 */
  refresh: () => Promise<void>;
}

let refreshSeq = 0;

export const useSessionsStore = create<SessionsState>((set) => ({
  list: [],
  loading: false,
  loaded: false,
  async refresh() {
    const seq = refreshSeq + 1;
    refreshSeq = seq;
    set({ loading: true });
    try {
      const list = await sessionsApi.list();
      if (seq === refreshSeq) {
        set({ list, loading: false, loaded: true });
      }
    } catch {
      // 列表拉取失败不弹技术错误，保留旧列表即可。
      if (seq === refreshSeq) {
        set({ loading: false });
      }
    }
  },
}));
