import {
  hasPermissions,
  listenForPermissionFlowFinished,
  requestPermissionFlow,
  type PermissionFlowRequest,
} from ".";

/**
 * 业务兜底只负责声明依赖并等待统一 flow；它不能发起具体系统权限申请。
 */
export async function waitForFeaturePermissions(
  input: PermissionFlowRequest,
  signal?: AbortSignal,
): Promise<boolean> {
  if (hasPermissions(input.permissionIds)) return true;
  if (signal?.aborted) return false;

  let finish: ((granted: boolean) => void) | null = null;
  const completed = new Promise<boolean>((resolve) => {
    finish = resolve;
  });
  const unlisten = await listenForPermissionFlowFinished((event) => {
    if (!event.requestIds.includes(input.requestId)) return;
    finish?.(hasPermissions(input.permissionIds));
  });
  const abort = () => finish?.(false);
  signal?.addEventListener("abort", abort, { once: true });

  try {
    if (signal?.aborted) return false;
    const ticket = await requestPermissionFlow(input);
    if (ticket.status === "noop") return hasPermissions(input.permissionIds);
    return await completed;
  } finally {
    signal?.removeEventListener("abort", abort);
    unlisten();
  }
}
