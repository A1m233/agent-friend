import { useEffect, useState } from "react";
import { openInnerActivity } from "@/lib/open-inner-activity";
import { toast } from "sonner";
import { Button, Input, Switch } from "@/components/ui";
import { useSetting, type Settings } from "@/lib/settings";
import { companionshipApi, type CompanionshipStatus } from "@/services/api/companionship";
import { SettingsRow } from "./SettingsRow";

type LimitKey = keyof Settings["companionshipLimits"];
const LIMITS: Array<{ key: LimitKey; label: string; min: number; max: number }> = [
  { key: "minimum_interval_minutes", label: "最短唤醒间隔（分钟）", min: 1, max: 1440 },
  { key: "interval_minutes", label: "最长唤醒间隔（分钟）", min: 10, max: 10080 },
];

export function CompanionshipSetting() {
  const [status, setStatus] = useState<CompanionshipStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [limits, setLimits] = useSetting("companionshipLimits");
  const [editing, setEditing] = useState<{ key: LimitKey; text: string } | null>(null);
  const commitInterval = (key: LimitKey, text: string, min: number, max: number) => {
    setEditing(null);
    const value = Number(text);
    const next = { ...limits, [key]: value };
    if (!text.trim() || !Number.isInteger(value) || value < min || value > max ||
        next.interval_minutes < next.minimum_interval_minutes) {
      toast.error("请输入有效的唤醒间隔", { description: `可用范围为 ${min} 到 ${max} 分钟，最短间隔不能超过最长间隔。` });
      return;
    }
    if (value !== limits[key]) void setLimits(next);
  };
  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const next = await companionshipApi.status();
        if (mounted) { setStatus(next); setError(null); }
      } catch (reason) { if (mounted) setError(String(reason)); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  const control = async (action: Parameters<typeof companionshipApi.control>[0]) => {
    setBusy(true);
    try { setStatus(await companionshipApi.control(action)); }
    catch (reason) { toast.error("自主活动状态未改变", { description: String(reason) }); }
    finally { setBusy(false); }
  };
  return <div className="flex flex-col gap-4">
    <SettingsRow label="自主陪伴">
      {status ? <Switch checked={status.enabled} disabled={busy}
        aria-label="开启自主陪伴" onCheckedChange={(enabled) => void control(enabled ? "enable" : "disable")} />
        : <span className="text-xs text-muted" role="status">{error ? "暂时不可用" : "正在读取…"}</span>}
    </SettingsRow>
    <p className="text-xs leading-relaxed text-muted">AF 会不时醒来，结合与你的交流探索、思考或准备小惊喜，自行决定是否分享。也可以什么都不做。主动消息可使用你已经绑定的 QQ；电脑休眠或关机时无法继续活动。</p>
    {error && <p className="text-xs text-warning">暂时无法读取活动状态：{error}</p>}
    {status && <>
      <p className="text-xs text-muted">{!status.enabled ? "已关闭" : status.activity_paused ? "活动已暂停" : status.active_run_id ? "正在自主活动" : "等待下一次唤醒"}
        {status.enabled && !status.activity_paused && status.next_wake_at ? ` · 下次机会：${new Date(status.next_wake_at).toLocaleString()}` : ""}
      </p>
      {status.enabled && status.last_outcome === "failed" && <p className="text-xs text-warning">上次活动未完成，可查看活动记录了解原因；之后会按间隔再尝试。</p>}
      {status.contact_paused && <p className="text-xs text-muted">主动联系已暂停，AF 可以继续思考，但不会向会话或 IM 投递新内容。</p>}
      <p className="text-xs text-muted">{status.available_im_destinations > 0
        ? `当前有 ${status.available_im_destinations} 个可用的 QQ 通知去向，AF 会选择合适的会话。`
        : "当前没有可用的 QQ 通知去向，内容仍可分享到普通会话。已有绑定离线时，请检查连接状态。"}</p>
      {status.recent_deliveries?.[0] && <p className="text-xs text-muted">最近分享：{
        status.recent_deliveries[0].conversation_state === "committed" ? "已写入会话" : status.recent_deliveries[0].conversation_state === "queued" ? "等待目标会话空闲" : "未投递到会话"
      } · {
        status.recent_deliveries[0].im_state === "delivered" ? "IM 已发送（不代表已读）"
          : status.recent_deliveries[0].im_state === "not_requested" ? "未通知 IM"
          : status.recent_deliveries[0].im_state === "pending" || status.recent_deliveries[0].im_state === "sending" ? "IM 等待结果"
          : status.recent_deliveries[0].im_state === "unknown" ? "IM 结果未知，不会自动重发" : "IM 未发送"
      }</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => void openInnerActivity()}>查看活动</Button>
      </div>
    </>}
    {LIMITS.map(({ key, label, min, max }) => <SettingsRow key={key} label={label}>
      <Input className="w-28" type="number" min={min} max={max} step={1} aria-label={label}
        value={editing?.key === key ? editing.text : limits[key]}
        onChange={(event) => setEditing({ key, text: event.target.value })}
        onBlur={(event) => commitInterval(key, event.currentTarget.value, min, max)}
        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
    </SettingsRow>)}
  </div>;
}
