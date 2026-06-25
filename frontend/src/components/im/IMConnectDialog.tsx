/**
 * IM 接入面板(022 design §3.9)。
 *
 * UX 设计:
 * - shadcn dialog,但 backdrop **透明**(不遮黑 pet 整屏 overlay 视觉环境)
 *   → 通过 DialogContent 的 overlayClassName 扩展点覆盖默认 backdrop
 * - 所有交互元素带 `data-hit`,让 pet 的 cursor passthrough 不吃掉点击
 * - dialog 主体 = 浮卡,bg-bg/95 + border + shadow,跟 ActionBar 同款
 *
 * 三段内容:
 * 1. 已绑定列表(mount 时拉 /v1/im/providers;有则渲染,无则提示)
 * 2. 接入新 IM 入口(目前仅 QQ;其他 disabled)
 * 3. 扫码进行中状态:QR + 轮询 status,success 后刷已绑定列表
 *
 * 轮询节奏(design §4):未到 qr_ready 250ms 一次,qr_ready 后 1s 一次,
 * success/failed 停止。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Plug, RefreshCcw, Trash2 } from "lucide-react";
import QRCode from "qrcode";

import {
  Button,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui";
import {
  imApi,
  type IMType,
  type OnboardTaskState,
  type ProviderInfo,
} from "@/services";
import { cn } from "@/utils/cn";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const POLL_INTERVAL_PENDING_MS = 250;
const POLL_INTERVAL_QR_READY_MS = 1000;

export function IMConnectDialog({ open, onOpenChange }: Props) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [onboardTask, setOnboardTask] = useState<OnboardTaskState | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const refreshProviders = useCallback(async () => {
    setLoadingProviders(true);
    try {
      const list = await imApi.listProviders();
      setProviders(list);
    } catch {
      setErrorMsg("拉取已绑定列表失败,稍后重试。");
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  // dialog 打开时拉一次;关闭时清空 onboard / QR 状态
  useEffect(() => {
    if (open) {
      void refreshProviders();
      setErrorMsg(null);
    } else {
      clearPollTimer();
      setOnboardTask(null);
      setQrDataUrl(null);
      setErrorMsg(null);
    }
    return clearPollTimer;
  }, [open, refreshProviders, clearPollTimer]);

  // QR URL 拿到后渲染 dataURL
  useEffect(() => {
    if (onboardTask?.qr_url) {
      QRCode.toDataURL(onboardTask.qr_url, { margin: 1, width: 240 })
        .then(setQrDataUrl)
        .catch(() => setErrorMsg("二维码渲染失败,请重试。"));
    } else {
      setQrDataUrl(null);
    }
  }, [onboardTask?.qr_url]);

  // onboard 状态轮询
  useEffect(() => {
    if (!onboardTask) return;
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
        setErrorMsg("拉取扫码状态失败,稍后重试。");
        clearPollTimer();
      }
    }, interval);
    return clearPollTimer;
  }, [onboardTask, clearPollTimer, refreshProviders]);

  const startQQOnboard = useCallback(async () => {
    setErrorMsg(null);
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
      setErrorMsg("启动扫码失败,稍后重试。");
    }
  }, []);

  const unbind = useCallback(
    async (imType: IMType, bindId: string) => {
      try {
        await imApi.unbindProvider(imType, bindId);
        await refreshProviders();
      } catch {
        setErrorMsg("解绑失败,稍后重试。");
      }
    },
    [refreshProviders],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-hit
        showCloseButton={false}
        overlayClassName="bg-transparent"
        className="w-[min(420px,calc(100vw-2rem))] max-w-none rounded-2xl bg-bg/95 shadow-2xl"
      >
        <DialogTitle className="text-lg font-semibold text-fg">
          接入 IM
        </DialogTitle>
        <DialogDescription className="mt-1 text-sm text-fg/70">
          在 IM 里也能跟我聊。同人格,共享记忆。
        </DialogDescription>

        {/* === 已绑定 === */}
        <section className="mt-5">
          <header className="mb-2 flex items-center justify-between">
            <span className="text-xs uppercase tracking-wide text-fg/60">
              已绑定
            </span>
            <Button
              data-hit
              variant="ghost"
              size="icon-sm"
              onClick={() => void refreshProviders()}
              disabled={loadingProviders}
              aria-label="刷新"
            >
              <RefreshCcw className="size-4" />
            </Button>
          </header>
          {providers.length === 0 ? (
            <p className="text-sm text-fg/50">
              {loadingProviders ? "加载中…" : "暂无已绑定的 IM。"}
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
                    data-hit
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => void unbind(p.im_type, p.bind_id)}
                    aria-label="解绑"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* === 接入新 IM === */}
        <section className="mt-5">
          <header className="mb-2 text-xs uppercase tracking-wide text-fg/60">
            接入新 IM
          </header>
          {onboardTask ? (
            <OnboardProgress
              task={onboardTask}
              qrDataUrl={qrDataUrl}
              onCancel={() => setOnboardTask(null)}
            />
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button
                data-hit
                size="sm"
                onClick={() => void startQQOnboard()}
              >
                <Plug className="size-4" />
                QQ
              </Button>
              <Button data-hit size="sm" variant="ghost" disabled>
                飞书 (敬请期待)
              </Button>
              <Button data-hit size="sm" variant="ghost" disabled>
                Telegram (敬请期待)
              </Button>
            </div>
          )}
        </section>

        {errorMsg && (
          <p className="mt-4 text-sm text-danger" role="alert">
            {errorMsg}
          </p>
        )}

        <DialogClose
          data-hit
          className="absolute right-4 top-4 rounded-md p-1 text-fg/60 hover:bg-surface hover:text-fg focus:outline-none"
          aria-label="关闭"
        >
          ✕
        </DialogClose>
      </DialogContent>
    </Dialog>
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
        <p className="text-sm text-fg/70">正在向腾讯请求扫码链接…</p>
        <Button data-hit size="sm" variant="ghost" onClick={onCancel}>
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
          用手机 QQ 扫码,完成创建者专属 Bot 绑定。
        </p>
        <Button data-hit size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    );
  }

  if (task.status === "success") {
    return (
      <div className="flex flex-col items-center gap-2 py-3">
        <p className="text-sm text-success">绑定成功 · {task.bind_id_masked}</p>
        <Button data-hit size="sm" onClick={onCancel}>
          完成
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-2 py-3">
      <p className="text-sm text-danger">绑定失败 · {task.error ?? "未知错误"}</p>
      <Button data-hit size="sm" variant="ghost" onClick={onCancel}>
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
