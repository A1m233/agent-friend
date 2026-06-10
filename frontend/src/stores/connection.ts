/**
 * 连接 store：bridge 连接配置 + 健康态（design §4.3）。
 */

import { create } from "zustand";
import { BRIDGE_DEFAULT_URL } from "@/constants";
import { checkHealth } from "@/services";

export type ConnectionStatus = "unknown" | "online" | "offline";

interface ConnectionState {
  bridgeUrl: string;
  status: ConnectionStatus;
  /** 探测 bridge 健康（GET /healthz）。 */
  ping: () => Promise<void>;
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  bridgeUrl: BRIDGE_DEFAULT_URL,
  status: "unknown",
  async ping() {
    const ok = await checkHealth();
    set({ status: ok ? "online" : "offline" });
  },
}));
