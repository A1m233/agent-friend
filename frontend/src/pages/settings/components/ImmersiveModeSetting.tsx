import { Switch } from "@/components/ui";
import { useSetting } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

export function ImmersiveModeSetting() {
  const [immersiveMode, setImmersiveMode] = useSetting("immersiveMode");

  return (
    <SettingsRow
      label="沉浸式交流"
      tooltip="开启后，Echo 会以更自然的朋友身份与你交流，不主动使用 AI 或内部机制等技术化表述。关闭后，在相关问题中会坦诚说明身份和实际使用的上下文来源。"
      tooltipAriaLabel="沉浸式交流说明"
    >
      <Switch
        checked={immersiveMode}
        onCheckedChange={(checked) => void setImmersiveMode(checked)}
        aria-label="沉浸式交流"
      />
    </SettingsRow>
  );
}
