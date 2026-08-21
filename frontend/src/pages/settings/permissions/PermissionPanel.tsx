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
import { useCommandRuntimeStore } from "@/stores";
import { systemPreparationPlan } from "./systemPreparation";

const SANDBOX_STATUS_LABELS = {
  unsupported: "当前平台暂不支持",
  checking: "正在检测",
  setup_required: "尚未初始化",
  setting_up: "正在初始化",
  ready: "可用",
  repair_required: "需要修复",
} as const;

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
  const runtimeStatus = useCommandRuntimeStore((state) => state.runtimeStatus);
  const refreshCommandRuntime = useCommandRuntimeStore((state) => state.refresh);
  const settingUpSandbox = useCommandRuntimeStore((state) => state.settingUpSandbox);
  const setupError = useCommandRuntimeStore((state) => state.setupError);
  const setupWindowsSandbox = useCommandRuntimeStore((state) => state.setupWindowsSandbox);
  const sandbox = runtimeStatus?.sandbox_capability;
  const microphone = snapshot.items.find((item) => item.id === "microphone");
  const activeFlow = snapshot.activeFlow;
  const preparation = systemPreparationPlan(microphone, activeFlow, sandbox);
  const canStart = !busy
    && !settingUpSandbox
    && runtimeStatus !== null
    && (!activeFlow || activeFlow.phase === "awaiting_user");
  const canManuallyPrepareMicrophone = Boolean(
    !activeFlow && microphone && microphone.systemState !== "granted",
  );
  const status = useMemo(
    () => (microphone ? STATUS_LABELS[microphone.presentationState] : "正在读取"),
    [microphone],
  );

  const executePermissionFlow = async (
    flow: NonNullable<typeof snapshot.activeFlow>,
  ) => {
    for (const permissionId of flow.permissionIds) {
      if (permissionId !== "microphone") continue;
      const instruction = await beginPermissionAttempt(flow.flowId, permissionId);
      if (instruction === "web_media") {
        const result = await executeMicrophonePermissionAttempt();
        if (result.kind === "timeout") setFeedback("系统授权等待超时，请重新尝试。");
        if (result.kind === "failed") setFeedback("系统授权未能完成，请检查设备后重试。");
      } else if (instruction === "open_settings") {
        await openPermissionSystemSettings(permissionId);
      }
      await completePermissionAttempt(flow.flowId, permissionId);
    }
  };

  const beginPreparation = async (forceMicrophone = false) => {
    if (!preparation.required && !forceMicrophone) return;
    setBusy(true);
    setFeedback(null);
    try {
      let flow = activeFlow;
      const actions = forceMicrophone && !preparation.microphone
        ? ["microphone" as const, ...preparation.actions]
        : preparation.actions;
      for (const action of actions) {
        if (action === "microphone") {
          if (!flow) {
            const ticket = await requestPermissionFlow({
              requestId: requestId(),
              reason: "manual",
              permissionIds: ["microphone"],
              sourceWindow: "chat",
            });
            if (ticket.flowId) {
              flow = (await refreshPermissions()).activeFlow;
            }
          }
          if (flow) {
            await executePermissionFlow(flow);
          }
        } else {
          await setupWindowsSandbox();
        }
      }
      await refreshAll();
    } catch (error) {
      const message = String(error);
      if (!message.includes("permission_already_granted")) setFeedback(message);
      await refreshAll();
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

  const refreshAll = async () => {
    await Promise.all([refreshPermissions(), refreshCommandRuntime()]);
  };

  return (
    <SettingsPageLayout
      title="系统权限"
      actions={
        <Button
          variant="outline"
          size="sm"
          loading={snapshot.refreshing}
          onClick={() => void refreshAll()}
        >
          重新检测
        </Button>
      }
    >
      {preparation.required ? (
        <SettingsGroup
          title="需要处理的系统能力"
          description="开始后会按顺序处理当前尚未完成的系统授权与能力准备，已经满足的项目会自动跳过。"
        >
          <div className="space-y-1 text-sm text-muted">
            {preparation.microphone ? <p>麦克风权限</p> : null}
            {preparation.windowsSandbox ? <p>Windows 命令沙箱</p> : null}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {activeFlow ? (
              <Button
                variant="ghost"
                disabled={busy || activeFlow.phase !== "awaiting_user"}
                onClick={() => void deferFlow()}
              >
                暂不处理麦克风
              </Button>
            ) : null}
            <Button
              loading={busy || settingUpSandbox}
              disabled={!canStart}
              onClick={() => void beginPreparation()}
            >
              开始准备
            </Button>
          </div>
        </SettingsGroup>
      ) : null}

      <SettingsGroup
        title="命令沙箱"
        description="为本地命令提供受治理的 Windows 隔离环境。这里仅准备系统能力，不会批准任何具体命令、文件或网络访问。"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="font-medium text-fg">Windows 命令沙箱</div>
            <p className="text-sm text-muted">
              初始化或修复时可能显示 Windows 管理员确认。成功一次后由所有会话共享。
            </p>
          </div>
          <Badge variant={sandbox?.state === "ready" ? "default" : "secondary"}>
            {sandbox ? SANDBOX_STATUS_LABELS[sandbox.state] : "正在读取"}
          </Badge>
        </div>

        {sandbox?.last_error?.message || setupError ? (
          <p className="text-sm text-danger">
            {sandbox?.last_error?.message || setupError}
          </p>
        ) : null}

      </SettingsGroup>

      <SettingsGroup title="权限状态">
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

        {canManuallyPrepareMicrophone && !preparation.microphone ? (
          <div className="flex justify-end">
            <Button
              loading={busy}
              disabled={!canStart}
              onClick={() => void beginPreparation(true)}
            >
              处理麦克风权限
            </Button>
          </div>
        ) : null}

      </SettingsGroup>
    </SettingsPageLayout>
  );
}
