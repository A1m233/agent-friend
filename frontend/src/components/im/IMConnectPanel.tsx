/**
 * IM 接入面板(022 design §3.9)。
 *
 * 面板只承载 IM 状态与交互，不绑定 dialog/window 形态；桌宠 ActionBar 通过
 * `open_im_connect` 打开独立 Tauri 窗口，由窗口负责拖动、关闭即隐藏。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Plug, RefreshCcw, Trash2 } from "lucide-react";
import QRCode from "qrcode";

import { Button, Spinner } from "@/components/ui";
import {
  imApi,
  type IMType,
  type OnboardTaskState,
  type ProviderInfo,
} from "@/services";
import { cn } from "@/utils/cn";

interface Props {
  active?: boolean;
}

const POLL_INTERVAL_PENDING_MS = 250;
const POLL_INTERVAL_QR_READY_MS = 1000;

export function IMConnectPanel({ active = true }: Props) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [unbindingKey, setUnbindingKey] = useState<string | null>(null);
  const [startingOnboard, setStartingOnboard] = useState(false);
  const [onboardTask, setOnboardTask] = useState<OnboardTaskState | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const mainRef = useRef<HTMLElement | null>(null);
  const onboardProgressRef = useRef<HTMLDivElement | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const qrScrolledTaskRef = useRef<string | null>(null);

  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const resetTransientState = useCallback(() => {
    clearPollTimer();
    qrScrolledTaskRef.current = null;
    setStartingOnboard(false);
    setOnboardTask(null);
    setQrDataUrl(null);
    setErrorMsg(null);
  }, [clearPollTimer]);

  const refreshProviders = useCallback(async () => {
    if (!active) return;
    setLoadingProviders(true);
    try {
      const list = await imApi.listProviders();
      setProviders(list);
    } catch {
      setErrorMsg("拉取已绑定列表失败，稍后重试。");
    } finally {
      setLoadingProviders(false);
    }
  }, [active]);

  useEffect(() => {
    if (active) {
      setErrorMsg(null);
      void refreshProviders();
    } else {
      resetTransientState();
    }
    return clearPollTimer;
  }, [active, clearPollTimer, refreshProviders, resetTransientState]);

  useEffect(() => {
    if (onboardTask?.qr_url) {
      QRCode.toDataURL(onboardTask.qr_url, { margin: 1, width: 240 })
        .then(setQrDataUrl)
        .catch(() => setErrorMsg("二维码渲染失败，请重试。"));
    } else {
      setQrDataUrl(null);
    }
  }, [onboardTask?.qr_url]);

  useEffect(() => {
    if (!active || onboardTask?.status !== "qr_ready" || !qrDataUrl) return;
    if (qrScrolledTaskRef.current === onboardTask.task_id) return;

    const frame = window.requestAnimationFrame(() => {
      const scrollContainer = mainRef.current;
      const target = onboardProgressRef.current;
      if (!scrollContainer || !target) return;

      const containerRect = scrollContainer.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const fullyVisible =
        targetRect.top >= containerRect.top && targetRect.bottom <= containerRect.bottom;
      if (!fullyVisible) {
        const nextScrollTop =
          targetRect.bottom > containerRect.bottom
            ? scrollContainer.scrollTop + targetRect.bottom - containerRect.bottom
            : scrollContainer.scrollTop + targetRect.top - containerRect.top;
        scrollContainer.scrollTo({ top: nextScrollTop, behavior: "smooth" });
      }
      qrScrolledTaskRef.current = onboardTask.task_id;
    });

    return () => window.cancelAnimationFrame(frame);
  }, [active, onboardTask?.status, onboardTask?.task_id, qrDataUrl]);

  useEffect(() => {
    if (!active || !onboardTask) return;
    if (onboardTask.status === "success" || onboardTask.status === "failed") {
      clearPollTimer();
      if (onboardTask.status === "success") {
        void refreshProviders();
      }
      return;
    }
    const interval =
      onboardTask.status === "qr_ready"
        ? POLL_INTERVAL_QR_READY_MS
        : POLL_INTERVAL_PENDING_MS;
    pollTimerRef.current = setTimeout(async () => {
      try {
        const next = await imApi.getOnboardState(onboardTask.task_id);
        setOnboardTask(next);
      } catch {
        setErrorMsg("拉取扫码状态失败，稍后重试。");
        clearPollTimer();
      }
    }, interval);
    return clearPollTimer;
  }, [active, onboardTask, clearPollTimer, refreshProviders]);

  const startQQOnboard = useCallback(async () => {
    if (!active || startingOnboard) return;
    setErrorMsg(null);
    setStartingOnboard(true);
    try {
      const { task_id } = await imApi.startOnboard("qq");
      setOnboardTask({
        task_id,
        im_type: "qq",
        status: "pending",
        qr_url: null,
        bind_id_masked: null,
        error: null,
      });
    } catch {
      setErrorMsg("启动扫码失败，稍后重试。");
    } finally {
      setStartingOnboard(false);
    }
  }, [active, startingOnboard]);

  const unbind = useCallback(
    async (imType: IMType, bindId: string) => {
      if (!active || unbindingKey) return;
      const key = `${imType}:${bindId}`;
      setUnbindingKey(key);
      try {
        await imApi.unbindProvider(imType, bindId);
        await refreshProviders();
      } catch {
        setErrorMsg("解绑失败，稍后重试。");
      } finally {
        setUnbindingKey(null);
      }
    },
    [active, refreshProviders, unbindingKey],
  );

  return (
    <div className="flex h-full flex-col bg-bg text-fg">
      <header className="border-b border-border px-4 py-3">
        <h1 className="text-lg font-semibold text-fg">接入 IM</h1>
        <p className="mt-1 text-sm text-fg/70">
          在 IM 里也能跟我聊。同人格，共享记忆。
        </p>
      </header>

      <main ref={mainRef} className="flex min-h-0 flex-1 flex-col gap-6 overflow-auto p-4">
        <section>
          <header className="mb-2 flex items-center justify-between">
            <span className="text-xs uppercase tracking-wide text-fg/60">
              已绑定
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => void refreshProviders()}
              loading={loadingProviders}
              disabled={loadingProviders}
              aria-label="刷新"
            >
              {!loadingProviders && <RefreshCcw className="size-4" />}
            </Button>
          </header>
          {providers.length === 0 ? (
            <p className="text-sm text-fg/50">
              {loadingProviders ? "加载中..." : "暂无已绑定的 IM。"}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {providers.map((p) => (
                <li
                  key={`${p.im_type}-${p.bind_id}`}
                  className="flex items-center justify-between rounded-lg border border-border bg-surface px-3 py-2"
                >
                  <span className="text-sm text-fg">
                    {labelOfIMType(p.im_type)} · {p.bind_id_masked}
                    <span
                      className={cn(
                        "ml-2 text-xs",
                        p.status === "active" && "text-success",
                        p.status === "degraded" && "text-warning",
                        p.status === "error" && "text-danger",
                        p.status === "stopped" && "text-fg/50",
                      )}
                    >
                      {labelOfStatus(p.status)}
                    </span>
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => void unbind(p.im_type, p.bind_id)}
                    loading={unbindingKey === `${p.im_type}:${p.bind_id}`}
                    disabled={Boolean(unbindingKey)}
                    aria-label="解绑"
                  >
                    {unbindingKey !== `${p.im_type}:${p.bind_id}` && (
                      <Trash2 className="size-4" />
                    )}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <header className="mb-2 text-xs uppercase tracking-wide text-fg/60">
            接入新 IM
          </header>
          {onboardTask ? (
            <div ref={onboardProgressRef}>
              <OnboardProgress
                task={onboardTask}
                qrDataUrl={qrDataUrl}
                onCancel={() => setOnboardTask(null)}
              />
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                loading={startingOnboard}
                onClick={() => void startQQOnboard()}
              >
                {!startingOnboard && <Plug className="size-4" />}
                QQ
              </Button>
              {/* <Button size="sm" variant="ghost" disabled>
                飞书 (敬请期待)
              </Button>
              <Button size="sm" variant="ghost" disabled>
                Telegram (敬请期待)
              </Button> */}
            </div>
          )}
        </section>

        {errorMsg && (
          <p className="text-sm text-danger" role="alert">
            {errorMsg}
          </p>
        )}
      </main>
    </div>
  );
}

function OnboardProgress({
  task,
  qrDataUrl,
  onCancel,
}: {
  task: OnboardTaskState;
  qrDataUrl: string | null;
  onCancel: () => void;
}) {
  if (task.status === "pending") {
    return (
      <div className="flex flex-col items-center gap-2 py-4">
        <p className="flex items-center gap-2 text-sm text-fg/70">
          <Spinner data-icon="inline-start" />
          正在向腾讯请求扫码链接...
        </p>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    );
  }

  if (task.status === "qr_ready") {
    return (
      <div className="flex flex-col items-center gap-3 py-2">
        {qrDataUrl ? (
          <img
            src={qrDataUrl}
            alt="QQ Bot 绑定二维码"
            className="size-60 rounded-md border border-border bg-bg p-2"
          />
        ) : (
          <div className="size-60 rounded-md border border-border bg-surface" />
        )}
        <p className="text-sm text-fg/70">
          用手机 QQ 扫码，完成创建者专属 Bot 绑定。
        </p>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    );
  }

  if (task.status === "success") {
    return (
      <div className="flex flex-col items-center gap-2 py-3">
        <p className="text-sm text-success">绑定成功 · {task.bind_id_masked}</p>
        <Button size="sm" onClick={onCancel}>
          完成
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-2 py-3">
      <p className="text-sm text-danger">绑定失败 · {task.error ?? "未知错误"}</p>
      <Button size="sm" variant="ghost" onClick={onCancel}>
        重试
      </Button>
    </div>
  );
}

function labelOfIMType(t: IMType): string {
  return { qq: "QQ Bot" }[t] ?? t;
}

function labelOfStatus(s: ProviderInfo["status"]): string {
  return (
    {
      active: "在线",
      degraded: "短暂掉线",
      error: "异常",
      stopped: "已停止",
    } as const
  )[s];
}
