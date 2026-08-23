import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import {
  useSetting,
  type ReminderNotificationMode,
} from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

const MODE_DESCRIPTIONS: Record<ReminderNotificationMode, string> = {
  smart:
    "根据你最近的使用情况，决定是否通过 QQ 或桌宠额外提醒。提醒始终会保存在对应会话中。",
  all: "提醒保存在对应会话中，同时通过可用的 QQ 和桌宠通知。",
};

export function ReminderNotificationSetting() {
  const [mode, setMode] = useSetting("reminderNotificationMode");

  return (
    <div className="flex flex-col gap-3">
      <SettingsRow label="提醒发送方式">
        <Select
          value={mode}
          onValueChange={(value) => void setMode(value as ReminderNotificationMode)}
        >
          <SelectTrigger className="min-w-64" aria-label="提醒发送方式">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="smart">智能选择（推荐）</SelectItem>
            <SelectItem value="all">所有位置</SelectItem>
          </SelectContent>
        </Select>
      </SettingsRow>
      <p className="text-xs leading-relaxed text-muted">{MODE_DESCRIPTIONS[mode]}</p>
    </div>
  );
}
