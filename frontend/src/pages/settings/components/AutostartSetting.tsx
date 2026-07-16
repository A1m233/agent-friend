import { useCallback, useEffect, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { toast } from "sonner";
import { Switch } from "@/components/ui";
import {
  autostartErrorMessage,
  getAutostartStatus,
  setAutostartEnabled,
  type AutostartStatus,
} from "@/services/autostart";
import { SettingsRow } from "./SettingsRow";

export function AutostartSetting() {
  const [status, setStatus] = useState<AutostartStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      const next = await getAutostartStatus();
      if (currentRequest === requestId.current) setStatus(next);
    } catch (error) {
      if (currentRequest !== requestId.current) return;
      setStatus({
        supported: false,
        enabled: false,
        reason: autostartErrorMessage(error),
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
    const unlistenPromise = getCurrentWindow().onFocusChanged(({ payload }) => {
      if (payload) void refresh();
    });
    return () => {
      requestId.current += 1;
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, [refresh]);

  const update = async (enabled: boolean) => {
    setSaving(true);
    try {
      setStatus(await setAutostartEnabled(enabled));
    } catch (error) {
      toast.error("开机自启设置失败", {
        description: autostartErrorMessage(error),
      });
      await refresh();
    } finally {
      setSaving(false);
    }
  };

  const unavailableReason = status?.supported === false ? status.reason : null;

  return (
    <SettingsRow
      label="登录 Windows 后自动启动"
      tooltip={
        unavailableReason ??
        "启用后，登录 Windows 时会显示桌宠并启动本机服务，不会自动打开聊天或设置窗口。"
      }
      tooltipAriaLabel="开机自启说明"
    >
      <Switch
        checked={status?.enabled ?? false}
        disabled={!status || !status.supported || saving}
        onCheckedChange={(checked) => void update(checked)}
        aria-label="登录 Windows 后自动启动 agent-friend"
      />
    </SettingsRow>
  );
}
