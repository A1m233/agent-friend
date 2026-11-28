import { useEffect, useMemo, useState } from "react";
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
  const refreshStatus = useCommandRuntimeStore((state) => state.refreshStatus);
  const statusReadFailed = useCommandRuntimeStore((state) => state.statusReadFailed);
  const settingUpSandbox = useCommandRuntimeStore((state) => state.settingUpSandbox);
  const setupError = useCommandRuntimeStore((state) => state.setupError);
  const setupWindowsSandbox = useCommandRuntimeStore((state) => state.setupWindowsSandbox);
  const cancelSandboxRecovery = useCommandRuntimeStore((state) => state.cancelSandboxRecovery);
  const recovery = runtimeStatus?.recovery ?? runtimeStatus?.sandbox_capability.recovery;
  const sandbox = statusReadFailed ? undefined : runtimeStatus?.sandbox_capability;
  useEffect(() => {
    // The store exposes failed reads beside the retry action.
    void refreshStatus().catch(() => undefined);
  }, [refreshStatus]);
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
    await Promise.all([
      refreshPermissions().catch((error) => setFeedback(String(error))),
      refreshStatus().catch(() => undefined),
    ]);
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
            {sandbox ? SANDBOX_STATUS_LABELS[sandbox.state] : statusReadFailed ? "读取失败，可重新检测" : "正在读取"}
          </Badge>
        </div>

        {sandbox?.last_error?.message || setupError ? (
          <p className="text-sm text-danger">
            {sandbox?.last_error?.message || setupError}
          </p>
        ) : null}

        {recovery ? <div className="space-y-3 text-sm" role="status" aria-live="polite">
          <p>{recovery.result === null ? {
            reconciling: "正在核对之前的任务与资源…",
            stopping: "正在停止修复并确认资源收尾…",
            repairing: "正在修复环境；可能需要 Windows 管理员确认…",
            verifying: "正在执行沙箱验证命令…",
            finished: "正在确认修复结果…",
          }[recovery.phase] : {
            succeeded: "沙箱已验证可用，可以重新发起命令。之前结果不明的命令不会自动重跑。",
            blocked: "修复暂时受阻。处理下面的事项后，可以重新核对。",
            failed: "本次修复未完成，可以重新核对或导出诊断。",
            cancelled: "本次修复已取消；仍未确认结束的资源会继续列在下方。",
            interrupted: "上次修复被中断，需重新核对资源状态。",
          }[recovery.result]}</p>
          {recovery.blockers.length ? <ul className="list-disc space-y-1 pl-5 text-muted">
            {recovery.blockers.map((blocker, index) => <li key={blocker.operation_id ?? index}>
              {blocker.reason === "active_command"
                ? "仍有任务运行，请等待它完成，或回到对应会话停止该任务。"
                : blocker.reason === "lifecycle_owner_active"
                  ? "另一个应用实例正在使用这份沙箱状态，请先退出那个实例。"
                  : blocker.reason.includes("elevated")
                    ? "管理员准备操作尚未确认结束，请处理系统确认窗口后重新核对；仍无法确认时可重启 Windows。"
                    : "旧操作的资源尚未确认结束。可重新核对；仍无法确认时，请保存工作后重启 Windows，或导出诊断。"}
            </li>)}
          </ul> : null}
          <div className="flex flex-wrap justify-end gap-2">
            {recovery.result === null ? <Button variant="outline" size="sm"
              disabled={recovery.cancel_requested}
              onClick={() => void cancelSandboxRecovery().catch((error) => setFeedback(String(error)))}>
              {recovery.cancel_requested ? "正在取消" : "取消本次修复"}
            </Button> : recovery.available_actions.includes("recheck") ? <Button variant="outline" size="sm"
              disabled={settingUpSandbox} onClick={() => void setupWindowsSandbox(true)}>重新核对</Button> : null}
          </div>
        </div> : null}

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
