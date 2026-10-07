import {
  Ban, CalendarCheck, CircleCheck, CircleHelp, CirclePause, CirclePlay,
  CircleStop, CircleX, Clock, Hourglass, ListOrdered, LoaderCircle,
  MessageSquareWarning, ShieldQuestion, SkipForward, TimerOff, Trash2,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui";

const states: Record<string, { label: string; icon: LucideIcon; tone?: string }> = {
  active: { label: "已启用", icon: CirclePlay, tone: "text-accent" },
  paused: { label: "已暂停", icon: CirclePause },
  exhausted: { label: "计划已结束", icon: CalendarCheck },
  deleted: { label: "已删除", icon: Trash2 },
  needs_target: { label: "待选择会话", icon: MessageSquareWarning, tone: "text-warning" },
  pending: { label: "等待提交", icon: Clock },
  claimed: { label: "等待提交", icon: Hourglass },
  submitted: { label: "等待会话空闲", icon: ListOrdered },
  running: { label: "执行中", icon: LoaderCircle, tone: "text-accent" },
  completed: { label: "已完成", icon: CircleCheck, tone: "text-accent" },
  failed: { label: "失败", icon: CircleX, tone: "text-danger" },
  cancelled: { label: "已取消", icon: Ban },
  skipped: { label: "已跳过", icon: SkipForward },
  expired: { label: "已过期", icon: TimerOff },
  interrupted: { label: "已中断", icon: CircleStop, tone: "text-warning" },
  rejected: { label: "未接收", icon: Ban, tone: "text-warning" },
  waiting_for_approval: { label: "等待审批", icon: ShieldQuestion, tone: "text-warning" },
};

export function StatusBadge({ state }: { state: string }) {
  const { label, icon: Icon, tone = "text-muted" } = states[state] ?? {
    label: state,
    icon: CircleHelp,
  };
  return (
    <Badge variant="outline" className={`self-center text-xs font-normal ${tone}`}>
      <Icon className="size-3" aria-hidden="true" />
      {label}
    </Badge>
  );
}
