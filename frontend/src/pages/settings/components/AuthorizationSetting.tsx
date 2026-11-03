import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import { useSetting, type Settings } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

export function AuthorizationSetting() {
  const [mode, setMode] = useSetting("authorizationMode");
  return <div className="flex flex-col gap-3">
    <SettingsRow label="授权模式">
      <Select value={mode} onValueChange={(value) => void setMode(value as Settings["authorizationMode"])}>
        <SelectTrigger className="min-w-64" aria-label="授权模式"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="auto_review">帮我审批</SelectItem>
          <SelectItem value="full_access">完全授权</SelectItem>
        </SelectContent>
      </Select>
    </SettingsRow>
    <p className="text-xs leading-relaxed text-muted">
      {mode === "auto_review"
        ? "需要权限的操作会由独立审批者检查。自主活动遇到拒绝会停止该操作，不会打扰你申请批准。"
        : "允许 AF 直接执行所申请的文件、命令和桌面应用操作，包括修改或删除文件。硬性安全限制和系统权限仍然有效。"}
    </p>
    <p className="text-xs leading-relaxed text-muted">此设置仅用于自主陪伴。普通聊天可在输入框中选择本会话的授权模式。</p>
  </div>;
}
