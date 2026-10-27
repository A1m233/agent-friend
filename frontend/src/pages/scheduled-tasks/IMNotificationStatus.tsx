import type { IMNotification } from "@/services/api/scheduledTasks";

const reasons: Record<string, string> = {
  no_binding: "会话没有绑定 IM",
  outcome_not_notified: "取消、跳过或过期的运行不发送通知",
  im_runtime_unavailable: "IM 服务不可用",
  result_unavailable: "无法读取本次执行结果",
  provider_unavailable: "IM 通道不可用",
  runtime_stopped: "IM 服务已停止",
  binding_changed: "已解绑或切换会话",
  platform_rejected: "平台拒绝发送",
  send_timeout: "发送超时，是否送达未知",
  send_exception: "发送异常，是否送达未知",
  send_in_progress: "发送已发起，尚无确认回执",
  provider_no_receipt: "平台未返回确认回执",
  missing_receipt: "平台未返回确认回执",
};
const states = {
  pending: "等待发送", delivered: "已发送", failed: "发送失败",
  unknown: "送达结果未知", skipped: "已跳过",
};

function targetLabel(destination: string): string {
  const [platform, , scope, chat] = destination.split("|");
  if (!chat) return "IM 目标";
  const suffix = chat.length > 4 ? chat.slice(-4) : "";
  return `${platform.toUpperCase()} ${scope === "c2c" ? "私聊" : "聊天"} · …${suffix}`;
}

export function IMNotificationStatus({ notification }: { notification?: IMNotification }) {
  if (!notification || notification.state === "disabled") return null;
  const label = notification.state === "pending" ? "等待执行结果"
    : notification.state === "ready" ? "正在处理"
    : notification.state === "skipped" ? "已跳过"
    : notification.state === "failed" ? "处理失败" : "投递结果";
  return (
    <div className="mt-2 space-y-1 text-xs text-muted">
      <p>IM 通知：{label}{notification.reason ? ` · ${reasons[notification.reason] ?? notification.reason}` : ""}</p>
      {notification.deliveries.map((item) => (
        <p key={item.destination_id}>
          {targetLabel(item.destination_id)}：{states[item.state]}
          {item.error_kind ? ` · ${reasons[item.error_kind] ?? item.error_kind}` : ""}
        </p>
      ))}
    </div>
  );
}
