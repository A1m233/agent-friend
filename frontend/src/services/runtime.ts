import { invoke } from "@tauri-apps/api/core";
import type { LocalService } from "@/constants";
import { isTauri } from "@/utils/tauri";

const readiness = new Map<LocalService, Promise<void>>();
const failures = new Map<
  LocalService,
  { error: unknown; attempts: number; retryAt: number }
>();
const RETRY_BASE_DELAY_MS = 5_000;
const RETRY_MAX_DELAY_MS = 60_000;

/**
 * 在 Tauri dev 与安装版中都通过同一个 Rust supervisor 确保本机服务就绪。
 * 浏览器调试仍由 scripts/dev 负责启动服务，因此保持 no-op。
 */
export function ensureLocalServiceReady(service: LocalService): Promise<void> {
  if (!isTauri()) return Promise.resolve();

  const pending = readiness.get(service);
  if (pending) return pending;

  const failure = failures.get(service);
  if (failure && Date.now() < failure.retryAt) {
    return Promise.reject(failure.error);
  }

  const start = invoke("start_runtime_service", { service })
    .then(() => {
      failures.delete(service);
      return undefined;
    })
    .catch((error) => {
      readiness.delete(service);
      const attempts = (failures.get(service)?.attempts ?? 0) + 1;
      const delay = Math.min(
        RETRY_BASE_DELAY_MS * 2 ** (attempts - 1),
        RETRY_MAX_DELAY_MS,
      );
      failures.set(service, { error, attempts, retryAt: Date.now() + delay });
      throw error;
    });
  readiness.set(service, start);
  return start;
}

/** 配置已持久化后，重启对应本机服务并刷新 readiness 缓存。 */
export async function restartLocalService(service: LocalService): Promise<void> {
  resetLocalServiceReadiness(service);
  if (!isTauri()) return;
  await invoke("restart_runtime_service", { service });
}

/** 配置变化等已知恢复动作后，允许对应服务立即重新拉起。 */
export function resetLocalServiceReadiness(service?: LocalService): void {
  if (service) {
    readiness.delete(service);
    failures.delete(service);
    return;
  }
  readiness.clear();
  failures.clear();
}
