import { useCallback, useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { ExternalLink, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  StatusDot,
} from "@/components/ui";
import {
  describePowerPolicy,
  getPowerPolicySnapshot,
  listenPowerPolicy,
  openPowerSettings,
  powerSourceLabel,
  refreshPowerPolicy,
  type PowerPolicySnapshot,
} from "@/lib/power-policy";
import {
  useSetting,
  type BackgroundOnlinePolicy,
} from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

export function BackgroundOnlineSetting() {
  const [policy, setPolicy] = useSetting("backgroundOnlinePolicy");
  const [snapshot, setSnapshot] = useState<PowerPolicySnapshot | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setSnapshot(await refreshPowerPolicy());
    } catch (error) {
      toast.error("后台在线状态刷新失败", { description: String(error) });
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    void getPowerPolicySnapshot()
      .then((next) => {
        if (mounted) setSnapshot(next);
      })
      .catch((error) => {
        if (mounted) toast.error("后台在线状态读取失败", { description: String(error) });
      });
    const unlistenState = listenPowerPolicy((next) => {
      if (mounted) setSnapshot(next);
    });
    const unlistenFocus = getCurrentWindow().onFocusChanged(({ payload }) => {
      if (payload) void refresh();
    });
    return () => {
      mounted = false;
      void unlistenState.then((unlisten) => unlisten());
      void unlistenFocus.then((unlisten) => unlisten());
    };
  }, [refresh]);

  const presentation = snapshot ? describePowerPolicy(snapshot) : null;
  const manualPath =
    snapshot?.platform === "windows"
      ? "合盖设置：控制面板 → 电源选项 → 选择关闭盖子的功能。"
      : snapshot?.platform === "macos"
        ? "合盖与睡眠设置：系统设置 → 电池 → 选项/锁定屏幕。"
        : "合盖和睡眠行为请在系统电源设置中调整。";

  const openSettings = async () => {
    try {
      await openPowerSettings();
    } catch (error) {
      toast.error("无法打开系统电源设置", {
        description: `${String(error)} ${manualPath}`,
      });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <SettingsRow
        label="后台在线策略"
        tooltip="只在 Agent 正在执行任务或 QQ 需要保持在线时阻止空闲休眠；显示器仍可按系统设置关闭。"
      >
        <Select
          value={policy}
          onValueChange={(value) => void setPolicy(value as BackgroundOnlinePolicy)}
        >
          <SelectTrigger className="min-w-64" aria-label="后台在线策略">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="followSystem">跟随系统</SelectItem>
            <SelectItem value="pluggedIn">仅接通电源时保持在线（推荐）</SelectItem>
            <SelectItem value="always">始终保持在线</SelectItem>
          </SelectContent>
        </Select>
      </SettingsRow>

      {policy === "always" ? (
        <p className="text-xs leading-relaxed text-warning">
          电池供电时也会保持系统唤醒，可能增加耗电和发热。合盖放入包内前请退出应用或改回跟随系统。
        </p>
      ) : null}

      <div className="flex items-start justify-between gap-4 rounded-md bg-bg p-4">
        <div className="flex min-w-0 items-start gap-2">
          <StatusDot
            className="mt-1.5"
            tone={presentation?.tone ?? "muted"}
            pulse={snapshot?.inhibitorState === "active"}
          />
          <div className="min-w-0">
            <p className="text-sm font-medium text-fg">
              {presentation?.label ?? "正在读取后台在线状态"}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              {presentation?.detail ?? "请稍候。"}
            </p>
            <p className="mt-1 text-xs text-muted">
              {snapshot ? powerSourceLabel(snapshot.powerSource) : "正在读取供电状态"}
            </p>
          </div>
        </div>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          loading={refreshing}
          onClick={() => void refresh()}
          aria-label="刷新后台在线状态"
        >
          <RefreshCw />
        </Button>
      </div>

      <SettingsRow
        label="系统电源设置"
        tooltip="应用不会自动修改电源计划或合盖动作。手动睡眠、低电量保护以及按系统策略合盖仍会暂停 Agent 和 IM。"
      >
        <Button type="button" variant="outline" size="sm" onClick={() => void openSettings()}>
          <ExternalLink />
          打开系统设置
        </Button>
      </SettingsRow>
      <p className="text-xs leading-relaxed text-muted">{manualPath}</p>
    </div>
  );
}
