import { useMemo, useState } from "react";
import { Badge, Button } from "@/components/ui";
import {
  beginPermissionAttempt,
  completePermissionAttempt,
  deferPermissionFlow,
  openPermissionSystemSettings,
  refreshPermissions,
  requestPermissionFlow,
  usePermissionSnapshot,
  type PermissionItemSnapshot,
} from "@/lib/permissions";
import { executeMicrophonePermissionAttempt } from "@/services/permissions/microphone";
import { SettingsGroup } from "../components/SettingsGroup";
import { SettingsPageLayout } from "../components/SettingsPageLayout";

const STATUS_LABELS: Record<PermissionItemSnapshot["presentationState"], string> = {
  not_requested: "尚未处理",
  granted: "已授权",
  denied: "已拒绝",
  revoked: "授权已失效",
  restricted: "受系统限制",
  unsupported: "当前平台暂不支持",
  unknown: "状态待确认",
};

function requestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `permission-${Date.now()}`;
}

export function PermissionPanel() {
  const snapshot = usePermissionSnapshot();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const microphone = snapshot.items.find((item) => item.id === "microphone");
  const activeFlow = snapshot.activeFlow;
  const canStart = Boolean(activeFlow && activeFlow.phase === "awaiting_user");
  const status = useMemo(
    () => (microphone ? STATUS_LABELS[microphone.presentationState] : "正在读取"),
    [microphone],
  );

  const beginFlow = async () => {
    if (!activeFlow) return;
    setBusy(true);
    setFeedback(null);
    try {
      for (const permissionId of activeFlow.permissionIds) {
        if (permissionId !== "microphone") continue;
        const instruction = await beginPermissionAttempt(activeFlow.flowId, permissionId);
        if (instruction === "web_media") {
          const result = await executeMicrophonePermissionAttempt();
          if (result.kind === "timeout") setFeedback("系统授权等待超时，请重新尝试。");
          if (result.kind === "failed") setFeedback("系统授权未能完成，请检查设备后重试。");
        } else if (instruction === "open_settings") {
          await openPermissionSystemSettings(permissionId);
        }
        await completePermissionAttempt(activeFlow.flowId, permissionId);
      }
    } catch (error) {
      const message = String(error);
      if (!message.includes("permission_already_granted")) setFeedback(message);
      await refreshPermissions();
    } finally {
      setBusy(false);
    }
  };

  const createManualFlow = async () => {
    setBusy(true);
    setFeedback(null);
    try {
      await requestPermissionFlow({
        requestId: requestId(),
        reason: "manual",
        permissionIds: ["microphone"],
        sourceWindow: "settings",
      });
    } catch (error) {
      setFeedback(String(error));
    } finally {
      setBusy(false);
    }
  };

  const deferFlow = async () => {
    if (!activeFlow) return;
    setBusy(true);
    setFeedback(null);
    try {
      await deferPermissionFlow(activeFlow.flowId);
    } catch (error) {
      setFeedback(String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsPageLayout
      title="系统权限"
      actions={
        <Button
          variant="outline"
          size="sm"
          loading={snapshot.refreshing}
          onClick={() => void refreshPermissions()}
        >
          重新检测
        </Button>
      }
    >
      <SettingsGroup title={activeFlow ? "需要处理的权限" : "权限状态"}>
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="font-medium text-fg">麦克风</div>
            <p className="text-sm text-muted">
              用于语音输入和语音通话。只有你明确开始语音功能时才会采集声音。
            </p>
          </div>
          <Badge variant={microphone?.systemState === "granted" ? "default" : "secondary"}>
            {status}
          </Badge>
        </div>

        {feedback ? <p className="text-sm text-danger">{feedback}</p> : null}

        <div className="flex flex-wrap justify-end gap-2">
          {activeFlow ? (
            <>
              <Button
                variant="ghost"
                disabled={busy || !canStart}
                onClick={() => void deferFlow()}
              >
                暂不处理
              </Button>
              <Button loading={busy} disabled={!canStart} onClick={() => void beginFlow()}>
                开始授权
              </Button>
            </>
          ) : microphone?.systemState === "granted" ? null : (
            <Button loading={busy} onClick={() => void createManualFlow()}>
              处理麦克风权限
            </Button>
          )}

          {!activeFlow && microphone?.recoveryAction === "open_system_settings" ? (
            <Button
              variant="outline"
              onClick={() => void openPermissionSystemSettings("microphone")}
            >
              打开系统设置
            </Button>
          ) : null}
        </div>
      </SettingsGroup>
    </SettingsPageLayout>
  );
}
