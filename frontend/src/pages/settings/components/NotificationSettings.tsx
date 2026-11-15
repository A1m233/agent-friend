import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from "@/components/ui";
import { useSetting, type NotificationCompletionMode } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";
import { NotificationSoundSettings } from "./NotificationSoundSettings";

export function NotificationSettings() {
  const [completion, setCompletion] = useSetting("notificationCompletionMode");
  const [sound, setSound] = useSetting("notificationSoundEnabled");

  return <div className="flex flex-col gap-4">
    <SettingsRow label="会话新回复">
      <Select value={completion} onValueChange={(value) => void setCompletion(value as NotificationCompletionMode)}>
        <SelectTrigger className="min-w-64" aria-label="会话新回复提示"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="whenNotViewing">未查看该会话时</SelectItem>
          <SelectItem value="always">始终提示</SelectItem>
          <SelectItem value="never">不提示</SelectItem>
        </SelectContent>
      </Select>
    </SettingsRow>
    <SettingsRow label="气泡提示音">
      <Switch checked={sound} onCheckedChange={(value) => void setSound(value)} aria-label="气泡提示音" />
    </SettingsRow>
    <NotificationSoundSettings />
    <p className="text-xs leading-relaxed text-muted">
      收到新通知时播放一次提示音。滚动查看、展开或补位不会再次播放。
    </p>
  </div>;
}
