import { invoke } from "@tauri-apps/api/core";
import type { LocalService } from "@/constants";
import { isTauri } from "@/utils/tauri";

const readiness = new Map<LocalService, Promise<void>>();

/**
 * 在 Tauri dev 与安装版中都通过同一个 Rust supervisor 确保本机服务就绪。
 * 浏览器调试仍由 scripts/dev 负责启动服务，因此保持 no-op。
 */
export function ensureLocalServiceReady(service: LocalService): Promise<void> {
  if (!isTauri()) return Promise.resolve();

  const pending = readiness.get(service);
  if (pending) return pending;

  const start = invoke("start_runtime_service", { service })
    .then(() => undefined)
    .catch((error) => {
      readiness.delete(service);
      throw error;
    });
  readiness.set(service, start);
  return start;
}

/** 仅供测试隔离；运行时失败会自动清除对应缓存以允许重试。 */
export function resetLocalServiceReadinessForTest(): void {
  readiness.clear();
}
