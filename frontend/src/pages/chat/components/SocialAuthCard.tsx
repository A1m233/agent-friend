import { useMemo } from "react";
import { Button, Spinner, StatusDot, type StatusDotProps } from "@/components/ui";
import {
  socialAuthApi,
  type SocialAuthRequestStatus,
} from "@/services";
import { useSocialAuthRequestsStore } from "@/stores";
import type { ToolBlock } from "@/types/chat";

const ACTIVE = new Set<SocialAuthRequestStatus>([
  "pending",
  "login_in_progress",
  "authenticated",
  "continuing",
]);

const STATUS_META: Record<
  SocialAuthRequestStatus,
  { label: string; detail: string; tone: StatusDotProps["tone"]; pulse?: boolean }
> = {
  pending: {
    label: "等待确认",
    detail: "登录只会在这台电脑上启动。",
    tone: "accent",
  },
  login_in_progress: {
    label: "正在登录",
    detail: "请在打开的浏览器或二维码窗口中完成登录。",
    tone: "accent",
    pulse: true,
  },
  authenticated: {
    label: "登录成功",
    detail: "正在恢复原任务。",
    tone: "success",
    pulse: true,
  },
  continuing: {
    label: "继续处理中",
    detail: "Agent 正在根据原目标重新判断并调用所需能力。",
    tone: "accent",
    pulse: true,
  },
  completed: {
    label: "已完成",
    detail: "登录后的原任务已经继续执行。",
    tone: "success",
  },
  declined: {
    label: "已暂缓",
    detail: "没有启动平台登录。",
    tone: "muted",
  },
  cancelled: {
    label: "已取消",
    detail: "登录流程已取消。",
    tone: "muted",
  },
  expired: {
    label: "已过期",
    detail: "这次授权请求已经过期，需要时请重新发起任务。",
    tone: "muted",
  },
  login_failed: {
    label: "登录未完成",
    detail: "没有取得可用登录状态，可以稍后重新尝试。",
    tone: "danger",
  },
  continuation_failed: {
    label: "继续执行失败",
    detail: "登录已完成，但原任务没有成功恢复。可以直接让 Agent 再试一次。",
    tone: "danger",
  },
};

export function SocialAuthCard({
  block,
  historySummary,
  isPrimaryActivity = true,
}: {
  block?: ToolBlock;
  historySummary?: { request: Record<string, unknown>; result: Record<string, unknown> };
  isPrimaryActivity?: boolean;
}) {
  const initial = useMemo(() => historySummary ? {
    requestId: asString(historySummary.result.request_id),
    platform: asString(historySummary.result.platform) || asString(historySummary.request.platform),
    reason: asString(historySummary.request.reason),
    originSurface: asString(historySummary.result.origin_surface),
  } : block ? parseToolBlock(block) : { requestId: "", platform: "", reason: "", originSurface: "" },
  [block, historySummary]);
  const entry = useSocialAuthRequestsStore((state) => (
    initial.requestId ? state.entries[initial.requestId] : undefined
  ));
  const perform = useSocialAuthRequestsStore((state) => state.perform);
  const snapshot = entry?.snapshot ?? null;
  const busy = entry?.submitting ?? false;
  const actionError = entry?.error ?? "";
  const status = snapshot?.status ?? "pending";
  const meta = STATUS_META[status];
  const passive = !isPrimaryActivity && ACTIVE.has(status);

  const platform = snapshot?.platform ?? initial.platform;
  const reason = snapshot?.reason ?? initial.reason;
  const fromIm = (snapshot?.originSurface ?? initial.originSurface).startsWith("im:");
  const challengeKind = snapshot?.challengeKind ?? null;
  const detail = getSocialAuthStatusDetail({
    status,
    platform,
    challengeKind,
    errorKind: snapshot?.errorKind ?? null,
    fallback: meta.detail,
  });

  return (
    <div
      className="max-w-[85%] rounded-lg border border-border bg-surface/60 px-3 py-3 text-sm"
      data-primary-activity={isPrimaryActivity && ACTIVE.has(status) ? "socialAuth" : undefined}
    >
      <div className="flex items-center gap-2">
        <StatusDot tone={meta.tone} pulse={isPrimaryActivity && meta.pulse} />
        <span className="font-medium text-fg">
          {platform === "xiaohongshu" ? "小红书登录" : "Bilibili 登录"}
        </span>
        <span className="ml-auto text-xs text-muted">{passive ? "登录请求" : meta.label}</span>
      </div>
      {reason && <p className="mt-2 text-fg">{reason}</p>}
      <p className="mt-1 text-xs text-muted">
        {passive ? "登录请求已记录，当前主要活动见高亮状态。" : detail}
      </p>
      {fromIm && (
        <p className="mt-1 text-xs text-muted">
          这项任务来自 IM；电脑端完成授权后，结果会回到原 IM 会话。
        </p>
      )}
      {actionError && <p className="mt-2 text-xs text-danger">{actionError}</p>}
      {status === "pending" && initial.requestId && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            type="button"
            size="xs"
            loading={busy}
            onClick={() => void perform(initial.requestId, "approve")}
          >
            开始登录
          </Button>
          <Button
            type="button"
            size="xs"
            variant="outline"
            disabled={busy}
            onClick={() => void perform(initial.requestId, "decline")}
          >
            暂不登录
          </Button>
        </div>
      )}
      {status === "login_in_progress" && initial.requestId && (
        <div className="mt-3 flex flex-col items-start gap-3">
          {platform === "bilibili" &&
            (challengeKind === "qr_image" ? (
              <img
                src={socialAuthApi.challengeUrl(initial.requestId)}
                alt="Bilibili 登录二维码"
                className="size-60 rounded-md border border-border bg-bg p-2"
              />
            ) : (
              <div className="flex size-60 items-center justify-center rounded-md border border-border bg-surface text-muted">
                <span className="flex items-center gap-2 text-xs">
                  {!passive && <Spinner data-icon="inline-start" />}
                  {passive ? "登录二维码待就绪" : "正在生成二维码"}
                </span>
              </div>
            ))}
          <Button
            type="button"
            size="xs"
            variant="outline"
            loading={busy}
            onClick={() => void perform(initial.requestId, "cancel")}
          >
            取消登录
          </Button>
        </div>
      )}
    </div>
  );
}

export function getSocialAuthStatusDetail({
  status,
  platform,
  challengeKind,
  errorKind,
  fallback,
}: {
  status: SocialAuthRequestStatus;
  platform: string;
  challengeKind: "qr_image" | null;
  errorKind: string | null;
  fallback: string;
}): string {
  if (status === "login_in_progress" && platform === "bilibili") {
    return challengeKind === "qr_image"
      ? "请用 Bilibili 手机客户端扫描下方二维码，并在手机端确认登录。"
      : "正在生成本次登录二维码。";
  }
  if (status === "login_in_progress" && platform === "xiaohongshu") {
    return "请在打开的小红书登录窗口中完成登录。";
  }
  if (status === "login_failed" && errorKind === "login_unavailable") {
    return "本机登录组件或凭据存储暂时不可用，可以稍后重新尝试。";
  }
  if (status === "login_failed" && errorKind === "auth_not_completed") {
    return "平台没有返回有效登录状态；二维码可能已过期，可以重新发起登录。";
  }
  return fallback;
}

function parseToolBlock(block: ToolBlock): {
  requestId: string;
  platform: string;
  reason: string;
  originSurface: string;
} {
  const args = parseObject(block.args);
  const result = parseObject(block.result ?? "");
  return {
    requestId: asString(result.request_id),
    platform: asString(result.platform) || asString(args.platform),
    reason: asString(args.reason),
    originSurface: asString(result.origin_surface),
  };
}

function parseObject(value: string): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}
