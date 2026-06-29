import { useMemo, useState } from "react";
import { RefreshCw, Save, Trash2 } from "lucide-react";
import {
  Badge,
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { DEEPSEEK_MODELS } from "@/lib/model-config";
import {
  describeRuntimeConfigEffect,
  type RuntimeConfigItemStatus,
  type RuntimeConfigSource,
  type RuntimeSecretName,
  useRuntimeConfig,
} from "@/lib/runtime-config";
import { useSetting, type DeepseekModel } from "@/lib/settings";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsRow } from "./SettingsRow";

const SECRET_LABELS: Record<RuntimeSecretName, string> = {
  DEEPSEEK_API_KEY: "DeepSeek API Key",
  TAVILY_API_KEY: "Tavily API Key",
  VOLC_ACCESS_KEY: "Volc Access Key",
  VOLC_SECRET_KEY: "Volc Secret Key",
  VOLC_RTC_APP_ID: "Volc RTC App ID",
  VOLC_RTC_APP_KEY: "Volc RTC App Key",
  VOLC_SPEECH_APP_ID: "Volc Speech App ID",
  VOLC_SPEECH_ACCESS_TOKEN: "Volc Speech Access Token",
  VOLC_SPEECH_API_KEY: "Volc Speech API Key",
};

const SOURCE_LABELS: Record<RuntimeConfigSource, string> = {
  env: "env",
  keyring: "keyring",
  "legacy-dotenv": ".env",
};

const VOLC_SECRETS: RuntimeSecretName[] = [
  "VOLC_ACCESS_KEY",
  "VOLC_SECRET_KEY",
  "VOLC_RTC_APP_ID",
  "VOLC_RTC_APP_KEY",
  "VOLC_SPEECH_APP_ID",
  "VOLC_SPEECH_ACCESS_TOKEN",
  "VOLC_SPEECH_API_KEY",
];

interface RuntimeSecretControlProps {
  name: RuntimeSecretName;
  status?: RuntimeConfigItemStatus;
  saving: boolean;
  onSave: (name: RuntimeSecretName, value: string) => Promise<unknown>;
  onDelete: (name: RuntimeSecretName) => Promise<unknown>;
}

interface SecretListProps {
  names: RuntimeSecretName[];
  statuses: Map<RuntimeSecretName, RuntimeConfigItemStatus>;
  savingName: RuntimeSecretName | null;
  onSave: (name: RuntimeSecretName, value: string) => Promise<unknown>;
  onDelete: (name: RuntimeSecretName) => Promise<unknown>;
}

function sourceLabel(source?: RuntimeConfigSource | null): string {
  return source ? SOURCE_LABELS[source] : "未配置";
}

function secretStatusText(status?: RuntimeConfigItemStatus): string {
  const effect = status?.effect ? describeRuntimeConfigEffect(status.effect) : "";
  if (!status?.configured) {
    return effect ? `未配置 · ${effect}` : "未配置";
  }
  return effect ? `已配置 · ${sourceLabel(status.source)} · ${effect}` : "已配置";
}

function secretHelperText(status?: RuntimeConfigItemStatus): string | null {
  if (!status?.configured) return null;
  if (status.source === "keyring") {
    return "配置值不会显示；输入新值会覆盖 keyring。";
  }
  return `当前值来自 ${sourceLabel(status.source)}；设置页只保存 keyring 覆盖值。`;
}

function RuntimeSecretControl({
  name,
  status,
  saving,
  onSave,
  onDelete,
}: RuntimeSecretControlProps) {
  const [draft, setDraft] = useState("");
  const configured = status?.configured ?? false;
  const canDelete = status?.source === "keyring";
  const helperText = secretHelperText(status);

  async function handleSave() {
    if (!draft.trim()) return;
    await onSave(name, draft);
    setDraft("");
  }

  async function handleDelete() {
    if (!canDelete) return;
    if (!window.confirm(`删除 ${SECRET_LABELS[name]}？`)) return;
    await onDelete(name);
  }

  return (
    <div className="grid gap-2 border-t border-border py-3 first:border-t-0 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{SECRET_LABELS[name]}</div>
          <div className="truncate text-xs text-muted">{secretStatusText(status)}</div>
        </div>
        <Badge variant={configured ? "default" : "secondary"}>
          {configured ? "已配置" : "未配置"}
        </Badge>
      </div>

      <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
        <div className="min-w-0 flex-1">
          <Input
            type="password"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="输入新值以保存或覆盖"
            aria-label={`${SECRET_LABELS[name]} 新值`}
            disabled={saving}
          />
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => void handleSave()}
            disabled={saving || !draft.trim()}
          >
            <Save className="size-4" />
            保存
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void handleDelete()}
            disabled={saving || !canDelete}
          >
            <Trash2 className="size-4" />
            删除
          </Button>
        </div>
      </div>

      {status?.error ? (
        <div className="text-xs text-danger">{status.error}</div>
      ) : null}
      {helperText ? <div className="text-xs text-muted">{helperText}</div> : null}
    </div>
  );
}

function SecretList({
  names,
  statuses,
  savingName,
  onSave,
  onDelete,
}: SecretListProps) {
  return (
    <div className="grid gap-0">
      {names.map((name) => (
        <RuntimeSecretControl
          key={name}
          name={name}
          status={statuses.get(name)}
          saving={savingName === name}
          onSave={onSave}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

export function RuntimeConfigPanel() {
  const [deepseekModel, setDeepseekModel] = useSetting("deepseekModel");
  const {
    snapshot,
    loading,
    savingName,
    error,
    refresh,
    saveSecret,
    removeSecret,
  } = useRuntimeConfig();

  const statuses = useMemo(
    () => new Map(snapshot.items.map((item) => [item.name, item] as const)),
    [snapshot.items],
  );

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-semibold">模型与凭据</h2>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={loading}
        >
          <RefreshCw className="size-4" />
          刷新
        </Button>
      </div>

      <SettingsGroup title="对话模型">
        <SettingsRow label="供应商">
          <Select value="deepseek" disabled>
            <SelectTrigger className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="deepseek">DeepSeek</SelectItem>
            </SelectContent>
          </Select>
        </SettingsRow>
        <SettingsRow label="主对话模型">
          <Select
            value={deepseekModel}
            onValueChange={(value) => void setDeepseekModel(value as DeepseekModel)}
          >
            <SelectTrigger className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DEEPSEEK_MODELS.map((model) => (
                <SelectItem key={model} value={model}>
                  {model}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingsRow>
        <SecretList
          names={["DEEPSEEK_API_KEY"]}
          statuses={statuses}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>

      {error ? <div className="text-sm text-danger">{error}</div> : null}

      <SettingsGroup title="Web Search">
        <SettingsRow label="供应商">
          <Select value="tavily" disabled>
            <SelectTrigger className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="tavily">Tavily</SelectItem>
            </SelectContent>
          </Select>
        </SettingsRow>
        <SecretList
          names={["TAVILY_API_KEY"]}
          statuses={statuses}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>

      <SettingsGroup title="语音通话与语音输入">
        <SettingsRow label="供应商">
          <Badge variant="secondary">火山引擎</Badge>
        </SettingsRow>
        <SecretList
          names={VOLC_SECRETS}
          statuses={statuses}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>
    </>
  );
}
