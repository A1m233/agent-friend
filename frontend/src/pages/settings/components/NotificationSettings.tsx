import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from "@/components/ui";
import { useSetting, type NotificationCompletionMode } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";
import { NotificationSoundSettings } from "./NotificationSoundSettings";

export function NotificationSettings() {
  const [completion, setCompletion] = useSetting("notificationCompletionMode");
  const [needsAction, setNeedsAction] = useSetting("notificationNeedsActionEnabled");
  const [failure, setFailure] = useSetting("notificationFailureEnabled");
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
    <SettingsRow label="需要你处理">
      <Switch checked={needsAction} onCheckedChange={(value) => void setNeedsAction(value)} aria-label="需要你处理时提示" />
    </SettingsRow>
    <SettingsRow label="异常结束">
      <Switch checked={failure} onCheckedChange={(value) => void setFailure(value)} aria-label="异常结束时提示" />
    </SettingsRow>
    <SettingsRow label="气泡提示音">
      <Switch checked={sound} onCheckedChange={(value) => void setSound(value)} aria-label="气泡提示音" />
    </SettingsRow>
    <NotificationSoundSettings />
    <p className="text-xs leading-relaxed text-muted">
      新气泡显示时播放提示音。关闭声音后，气泡仍会正常显示。
    </p>
  </div>;
}
