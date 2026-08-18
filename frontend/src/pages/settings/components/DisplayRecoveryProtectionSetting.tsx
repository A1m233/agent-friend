import { Switch } from "@/components/ui";
import { useSetting } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

export function DisplayRecoveryProtectionSetting() {
  const [enabled, setEnabled] = useSetting("displayRecoveryProtectionEnabled");

  return (
    <SettingsRow
      label="显示器恢复保护"
      tooltip="开启后，屏幕从熄灭状态恢复时会暂时隐藏桌宠，并在渲染安全检查通过后重新显示；检查失败时桌宠会保持隐藏，避免透明窗口异常形成黑色全屏遮罩。"
      tooltipAriaLabel="显示器恢复保护说明"
    >
      <Switch
        checked={enabled}
        onCheckedChange={(checked) => void setEnabled(checked)}
        aria-label="显示器恢复保护"
      />
    </SettingsRow>
  );
}
