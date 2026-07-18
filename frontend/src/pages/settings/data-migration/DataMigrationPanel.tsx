import { open, save } from "@tauri-apps/plugin-dialog";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button, ConfirmDialog, Input } from "@/components/ui";
import {
  commitMigrationPackage,
  clearLastMigrationResult,
  deleteLatestMigrationBackup,
  exportMigrationPackage,
  getLatestMigrationBackup,
  getLastMigrationResult,
  getMigrationInventory,
  preflightLatestMigrationBackup,
  preflightMigrationPackage,
  type MigrationBackupMetadata,
  type MigrationInventory,
  type MigrationCommitResult,
  type MigrationManifest,
  type PreflightMigrationResult,
} from "@/lib/dataMigration";
import { SettingsGroup } from "../components/SettingsGroup";
import { SettingsPageLayout } from "../components/SettingsPageLayout";

type PendingCommit = {
  preflight: PreflightMigrationResult;
  title: string;
};

type BusyState = "export" | "preflight" | "commit" | "backup" | null;

export function DataMigrationPanel() {
  const [inventory, setInventory] = useState<MigrationInventory | null>(null);
  const [backup, setBackup] = useState<MigrationBackupMetadata | null>(null);
  const [inputPath, setInputPath] = useState("");
  const [transferCode, setTransferCode] = useState("");
  const [generatedCode, setGeneratedCode] = useState("");
  const [busy, setBusy] = useState<BusyState>(null);
  const [pendingCommit, setPendingCommit] = useState<PendingCommit | null>(null);
  const [deleteBackupOpen, setDeleteBackupOpen] = useState(false);
  const [lastResult, setLastResult] = useState<MigrationCommitResult | null>(null);

  const refresh = useCallback(async () => {
    const [nextInventory, nextBackup, nextResult] = await Promise.all([
      getMigrationInventory(),
      getLatestMigrationBackup(),
      getLastMigrationResult(),
    ]);
    setInventory(nextInventory);
    setBackup(nextBackup);
    setLastResult(nextResult);
  }, []);

  useEffect(() => {
    void refresh().catch((error) => {
      toast.error("无法读取迁移状态", { description: errorMessage(error) });
    });
  }, [refresh]);

  const handleExport = async () => {
    const selected = await save({
      title: "保存 agent-friend 迁移包",
      defaultPath: `agent-friend-${new Date().toISOString().slice(0, 10)}.agentfriend-transfer`,
      filters: [{ name: "agent-friend 迁移包", extensions: ["agentfriend-transfer"] }],
    });
    if (!selected) return;
    setBusy("export");
    setGeneratedCode("");
    try {
      const result = await exportMigrationPackage(selected);
      setGeneratedCode(result.transferCode);
      toast.success("迁移包已生成", {
        description: "请将迁移码与迁移包分开保管。",
      });
      await refresh();
    } catch (error) {
      toast.error("无法生成迁移包", { description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const chooseImport = async () => {
    const selected = await open({
      title: "选择 agent-friend 迁移包",
      multiple: false,
      directory: false,
      filters: [{ name: "agent-friend 迁移包", extensions: ["agentfriend-transfer"] }],
    });
    if (typeof selected === "string") setInputPath(selected);
  };

  const handlePreflight = async () => {
    if (!inputPath || transferCode.trim().split(/\s+/).length !== 12) {
      toast.error("请选择迁移包并输入完整的 12 词迁移码");
      return;
    }
    setBusy("preflight");
    try {
      const preflight = await preflightMigrationPackage(inputPath, transferCode.trim());
      setPendingCommit({ preflight, title: "替换为迁入的数据？" });
    } catch (error) {
      toast.error("迁移包预检失败", { description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const commitPending = async () => {
    if (!pendingCommit) return;
    setBusy("commit");
    try {
      const result = await commitMigrationPackage(pendingCommit.preflight.operationId);
      setLastResult(result);
      toast.success("数据已恢复，应用即将重启");
      setPendingCommit(null);
    } catch (error) {
      toast.error("恢复失败，原数据已尝试回滚", {
        description: errorMessage(error),
      });
      setPendingCommit(null);
      setBusy(null);
      await refresh();
    }
  };

  const restoreBackup = async () => {
    setBusy("backup");
    try {
      const preflight = await preflightLatestMigrationBackup();
      setPendingCommit({ preflight, title: "恢复最近一次回滚备份？" });
    } catch (error) {
      toast.error("无法读取回滚备份", { description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const deleteBackup = async () => {
    setBusy("backup");
    try {
      await deleteLatestMigrationBackup();
      setBackup(null);
      setDeleteBackupOpen(false);
      toast.success("回滚备份已删除");
    } catch (error) {
      toast.error("无法删除回滚备份", { description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <SettingsPageLayout title="数据与迁移">
      <SettingsGroup title="迁移到新电脑">
        <p className="text-sm text-muted">
          生成一份加密迁移包，包含会话、记忆、人格、设置、系统凭据、QQ
          绑定和用户模型。迁移码不会写入文件。
        </p>
        {inventory ? <InventorySummary inventory={inventory} /> : null}
        <div>
          <Button loading={busy === "export"} disabled={busy !== null} onClick={handleExport}>
            生成迁移包
          </Button>
        </div>
        {generatedCode ? (
          <div className="flex flex-col gap-2 rounded-md border border-border bg-bg p-3">
            <p className="text-sm font-medium text-fg">迁移码</p>
            <Input readOnly value={generatedCode} aria-label="迁移码" />
            <div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void navigator.clipboard.writeText(generatedCode)}
              >
                复制迁移码
              </Button>
            </div>
            <p className="text-xs text-muted">请与迁移包分开保存；遗失后无法恢复该迁移包。</p>
          </div>
        ) : null}
      </SettingsGroup>

      <SettingsGroup title="从旧电脑恢复">
        <p className="text-sm text-muted">
          第一版只支持 Windows 到 Windows。新电脑已有数据时会整体替换，不会自动合并。
        </p>
        <div className="flex gap-2">
          <Input readOnly value={inputPath} placeholder="尚未选择迁移包" aria-label="迁移包路径" />
          <Button variant="outline" onClick={chooseImport} disabled={busy !== null}>
            选择文件
          </Button>
        </div>
        <Input
          value={transferCode}
          onChange={(event) => setTransferCode(event.target.value)}
          placeholder="输入 12 词迁移码"
          aria-label="输入迁移码"
          disabled={busy !== null}
        />
        <div>
          <Button
            loading={busy === "preflight"}
            disabled={busy !== null}
            onClick={handlePreflight}
          >
            检查并恢复
          </Button>
        </div>
      </SettingsGroup>

      <SettingsGroup title="最近回滚备份">
        {backup ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-fg">
              {new Date(backup.createdAt).toLocaleString()} · {formatBytes(backup.size)}
            </p>
            <p className="text-xs text-muted">只保留最近一份，不会按时间自动删除。</p>
            <div className="flex gap-2">
              <Button variant="outline" onClick={restoreBackup} disabled={busy !== null}>
                恢复备份
              </Button>
              <Button
                variant="destructive"
                onClick={() => setDeleteBackupOpen(true)}
                disabled={busy !== null}
              >
                删除备份
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted">还没有回滚备份。</p>
        )}
      </SettingsGroup>

      {lastResult ? (
        <SettingsGroup title="最近一次恢复结果">
          <p className="text-xs text-muted">
            完成于 {new Date(lastResult.completedAt).toLocaleString()}
          </p>
          <div className="flex flex-col gap-2">
            {lastResult.assets.map((asset) => (
              <p className="text-sm text-fg" key={asset.group}>
                {assetResultLabel(asset.group)} · {assetStatusLabel(asset.status)}
                <span className="text-muted"> — {asset.message}</span>
              </p>
            ))}
          </div>
          <div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void clearLastMigrationResult().then(() => setLastResult(null));
              }}
            >
              知道了
            </Button>
          </div>
        </SettingsGroup>
      ) : null}

      <ConfirmDialog
        open={pendingCommit !== null}
        title={pendingCommit?.title ?? "确认恢复"}
        description="当前设备的会话、记忆、人格、设置、凭据和用户模型将被整体替换。系统会先创建并验证新的回滚备份。"
        confirmLabel="确认整体替换"
        variant="destructive"
        confirmDelaySeconds={5}
        loading={busy === "commit"}
        onConfirm={() => void commitPending()}
        onCancel={() => setPendingCommit(null)}
      >
        {pendingCommit ? (
          <div className="flex flex-col gap-2">
            <PackageSummary manifest={pendingCommit.preflight.manifest} />
            <InventorySummary inventory={pendingCommit.preflight.destination} label="当前设备" />
          </div>
        ) : null}
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteBackupOpen}
        title="删除最近的回滚备份？"
        description="删除后无法再通过此备份撤销最近一次数据替换。"
        confirmLabel="删除备份"
        variant="destructive"
        confirmDelaySeconds={5}
        loading={busy === "backup"}
        onConfirm={() => void deleteBackup()}
        onCancel={() => setDeleteBackupOpen(false)}
      />
      {busy ? (
        <p className="text-xs text-muted" role="status">
          {busyMessage(busy)}
        </p>
      ) : null}
    </SettingsPageLayout>
  );
}

function InventorySummary({
  inventory,
  label = "本机资产",
}: {
  inventory: MigrationInventory;
  label?: string;
}) {
  return (
    <p className="text-xs text-muted">
      {label}：会话文件 {inventory.sessionFileCount} · 记忆文件 {inventory.memoryFileCount} ·
      人格文件 {inventory.personaFileCount} · 用户模型文件 {inventory.userModelFileCount} ·
      系统凭据 {inventory.managedCredentialCount} · QQ 绑定 {inventory.imCredentialCount} · 共{" "}
      {formatBytes(inventory.totalBytes)}
    </p>
  );
}

function PackageSummary({ manifest }: { manifest: MigrationManifest }) {
  const total = manifest.entries.reduce((sum, entry) => sum + entry.size, 0);
  return (
    <p className="text-xs text-muted">
      将迁入：{manifest.entries.length} 个资产文件 · {formatBytes(total)} · 来源版本{" "}
      {manifest.sourceAppVersion}
    </p>
  );
}

function busyMessage(busy: Exclude<BusyState, null>) {
  return {
    export: "正在停止写入并生成、验证加密迁移包…",
    preflight: "正在解密到暂存区并校验完整性与空间…",
    commit: "正在备份当前数据、整体替换并重建记忆索引，请勿退出应用…",
    backup: "正在处理最近一次回滚备份…",
  }[busy];
}

function assetResultLabel(group: string) {
  return (
    {
      sessions: "历史会话",
      memory: "长期记忆",
      persona: "人格",
      settings: "设置",
      userModels: "用户模型",
      apiCredentials: "API 密钥",
      imCredentials: "QQ 绑定",
    }[group] ?? group
  );
}

function assetStatusLabel(status: string) {
  return (
    {
      success: "成功",
      notPresent: "无需迁移",
      needsUserAction: "需要处理",
      failed: "失败",
    }[status] ?? status
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
