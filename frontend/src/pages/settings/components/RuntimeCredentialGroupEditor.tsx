import { useMemo, useState } from "react";
import { RotateCw, Save, Trash2, Undo2 } from "lucide-react";
import { Badge, Button, ConfirmDialog, Input } from "@/components/ui";
import {
  runtimeCredentialOperationBusy,
  type RuntimeConfigSource,
  type RuntimeCredentialFieldMutation,
  type RuntimeCredentialGroupSnapshot,
  type RuntimeSecretName,
} from "@/lib/runtime-config";

export const RUNTIME_SECRET_LABELS: Record<RuntimeSecretName, string> = {
  AMAP_WEBSERVICE_KEY: "高德 Web Service Key",
  DEEPSEEK_API_KEY: "DeepSeek API Key",
  KIMI_API_KEY: "Kimi API Key",
  TAVILY_API_KEY: "Tavily API Key",
  QWEATHER_API_KEY: "QWeather API Key",
  QWEATHER_API_HOST: "QWeather API Host",
  VOLC_ACCESS_KEY: "Volc Access Key",
  VOLC_SECRET_KEY: "Volc Secret Key",
  VOLC_RTC_APP_ID: "Volc RTC App ID",
  VOLC_RTC_APP_KEY: "Volc RTC App Key",
  VOLC_SPEECH_APP_ID: "Volc Speech App ID",
  VOLC_SPEECH_ACCESS_TOKEN: "Volc Speech Access Token",
  VOLC_SPEECH_API_KEY: "Volc Speech API Key（可选）",
};

const SOURCE_LABELS: Record<RuntimeConfigSource, string> = {
  env: "环境变量",
  keyring: "系统凭据库",
  "legacy-dotenv": ".env",
};

interface RuntimeCredentialGroupEditorProps {
  snapshot: RuntimeCredentialGroupSnapshot;
  validationHelp: string;
  onApply: (
    changes: RuntimeCredentialFieldMutation[],
  ) => Promise<RuntimeCredentialGroupSnapshot>;
  onConfirm: (operationId: string) => Promise<RuntimeCredentialGroupSnapshot>;
  onCancel: (operationId: string) => Promise<RuntimeCredentialGroupSnapshot>;
  onRetryApply: () => Promise<RuntimeCredentialGroupSnapshot>;
  onRetryValidation: () => Promise<RuntimeCredentialGroupSnapshot>;
}

export function runtimeCredentialStatusLabel(
  snapshot: RuntimeCredentialGroupSnapshot,
): "未配置" | "配置不完整" | "已配置" | "已配置 · 已验证" | "凭据有误" {
  if (snapshot.validation === "invalid") return "凭据有误";
  if (snapshot.partiallyConfigured) return "配置不完整";
  if (!snapshot.configured) return "未配置";
  return snapshot.validation === "valid" ? "已配置 · 已验证" : "已配置";
}

function statusBadge(snapshot: RuntimeCredentialGroupSnapshot) {
  const label = runtimeCredentialStatusLabel(snapshot);
  if (snapshot.validation === "invalid") {
    return <Badge variant="destructive">{label}</Badge>;
  }
  if (snapshot.partiallyConfigured) {
    return <Badge variant="secondary">{label}</Badge>;
  }
  if (!snapshot.configured) {
    return <Badge variant="secondary">{label}</Badge>;
  }
  return <Badge variant="default">{label}</Badge>;
}

function operationTone(snapshot: RuntimeCredentialGroupSnapshot): string {
  if (snapshot.operation.phase === "failed") return "text-danger";
  if (snapshot.operation.phase === "applyBlocked") return "text-warning";
  return "text-muted";
}

export function RuntimeCredentialGroupEditor({
  snapshot,
  validationHelp,
  onApply,
  onConfirm,
  onCancel,
  onRetryApply,
  onRetryValidation,
}: RuntimeCredentialGroupEditorProps) {
  const [drafts, setDrafts] = useState<Partial<Record<RuntimeSecretName, string>>>({});
  const [deleted, setDeleted] = useState<Set<RuntimeSecretName>>(() => new Set());
  const [confirming, setConfirming] = useState(false);
  const busy = runtimeCredentialOperationBusy(snapshot.operation) || confirming;
  const changes = useMemo<RuntimeCredentialFieldMutation[]>(() => {
    const next: RuntimeCredentialFieldMutation[] = [];
    for (const item of snapshot.items) {
      if (deleted.has(item.name)) {
        next.push({ name: item.name, action: "delete" });
        continue;
      }
      const value = drafts[item.name]?.trim();
      if (value) next.push({ name: item.name, action: "set", value });
    }
    return next;
  }, [deleted, drafts, snapshot.items]);

  function clearDrafts() {
    setDrafts({});
    setDeleted(new Set());
  }

  async function handleApply() {
    if (changes.length === 0) return;
    if (
      deleted.size > 0 &&
      !window.confirm("保存后会删除标记的系统凭据。确认继续吗？")
    ) {
      return;
    }
    const result = await onApply(changes);
    if (result.operation.persisted) clearDrafts();
  }

  async function handleConfirm() {
    const operationId = snapshot.operation.operationId;
    if (!operationId) return;
    setConfirming(true);
    try {
      const result = await onConfirm(operationId);
      if (result.operation.persisted) clearDrafts();
    } finally {
      setConfirming(false);
    }
  }

  async function handleCancel() {
    const operationId = snapshot.operation.operationId;
    if (!operationId) return;
    setConfirming(true);
    try {
      await onCancel(operationId);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs text-muted">
          {snapshot.missingRequiredFields.length > 0
            ? `缺少：${snapshot.missingRequiredFields
                .map((name) => RUNTIME_SECRET_LABELS[name])
                .join("、")}`
            : validationHelp}
        </div>
        {statusBadge(snapshot)}
      </div>

      <div className="grid gap-0">
        {snapshot.items.map((item) => {
          const markedForDeletion = deleted.has(item.name);
          return (
            <div
              key={item.name}
              className="grid gap-2 border-t border-border py-3 first:border-t-0 first:pt-0"
            >
              <div className="flex min-w-0 items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {RUNTIME_SECRET_LABELS[item.name]}
                  </div>
                  <div className="truncate text-xs text-muted">
                    {markedForDeletion
                      ? "保存后删除"
                      : item.configured
                        ? `已配置 · ${item.source ? SOURCE_LABELS[item.source] : "未知来源"}`
                        : "未配置"}
                  </div>
                </div>
                {item.source === "keyring" || markedForDeletion ? (
                  <Button
                    type="button"
                    size="xs"
                    variant={markedForDeletion ? "outline" : "destructive"}
                    disabled={busy}
                    onClick={() => {
                      setDeleted((current) => {
                        const next = new Set(current);
                        if (markedForDeletion) next.delete(item.name);
                        else next.add(item.name);
                        return next;
                      });
                    }}
                  >
                    {markedForDeletion ? (
                      <Undo2 className="size-3" />
                    ) : (
                      <Trash2 className="size-3" />
                    )}
                    {markedForDeletion ? "撤销删除" : "删除"}
                  </Button>
                ) : null}
              </div>
              <Input
                type="password"
                value={drafts[item.name] ?? ""}
                onChange={(event) => {
                  const value = event.target.value;
                  setDrafts((current) => ({ ...current, [item.name]: value }));
                  if (value) {
                    setDeleted((current) => {
                      const next = new Set(current);
                      next.delete(item.name);
                      return next;
                    });
                  }
                }}
                placeholder={item.configured ? "留空表示不修改" : "输入凭据"}
                aria-label={`${RUNTIME_SECRET_LABELS[item.name]} 新值`}
                disabled={busy || markedForDeletion}
              />
              {item.error ? <div className="text-xs text-danger">{item.error}</div> : null}
            </div>
          );
        })}
      </div>

      {snapshot.operation.phase !== "idle" ? (
        <div className={`text-xs ${operationTone(snapshot)}`}>
          {snapshot.operation.message}
        </div>
      ) : null}

      <div className="flex justify-end gap-2">
        {snapshot.credentialRevision &&
          snapshot.validation === "none" &&
          (snapshot.group === "deepseek" || snapshot.group === "kimi") && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void onRetryValidation()}
            >
              <RotateCw className="size-4" />
              校验当前凭据
            </Button>
          )}
        {(snapshot.operation.phase === "applyBlocked" ||
          (snapshot.operation.phase === "failed" && snapshot.operation.persisted)) && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void onRetryApply()}
          >
            <RotateCw className="size-4" />
            重试应用
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          loading={busy}
          disabled={busy || changes.length === 0}
          onClick={() => void handleApply()}
        >
          {!busy && <Save className="size-4" />}
          保存此组
        </Button>
      </div>

      <ConfirmDialog
        open={snapshot.operation.phase === "awaitingConfirmation"}
        title="当前无法完成凭据校验"
        description={snapshot.operation.message ?? undefined}
        confirmLabel="仍然保存"
        loading={confirming}
        onConfirm={() => void handleConfirm()}
        onCancel={() => void handleCancel()}
      />
    </div>
  );
}
