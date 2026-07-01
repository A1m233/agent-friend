import { useMemo } from "react";
import { RefreshCw } from "lucide-react";
import {
  Badge,
  Button,
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
import {
  RuntimeSecretList,
  type RuntimeSecretListItem,
} from "./RuntimeSecretList";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";
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

function secretItems({
  names,
  statuses,
}: {
  names: RuntimeSecretName[];
  statuses: Map<RuntimeSecretName, RuntimeConfigItemStatus>;
}): RuntimeSecretListItem[] {
  return names.map((name) => {
    const status = statuses.get(name);
    return {
      name,
      label: SECRET_LABELS[name],
      status,
      statusText: secretStatusText(status),
      helperText: secretHelperText(status),
    };
  });
}

export function RuntimeConfigPanel() {
  const [deepseekModel, setDeepseekModel] = useSetting("deepseekModel");
  const [deepseekMemoryModel, setDeepseekMemoryModel] = useSetting(
    "deepseekMemoryModel",
  );
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
    <SettingsPageLayout
      title="模型与凭据"
      actions={
        <Button
          type="button"
          variant="outline"
          size="xs"
          onClick={() => void refresh()}
          loading={loading}
          disabled={loading}
        >
          {!loading && <RefreshCw className="size-4" />}
          刷新
        </Button>
      }
    >
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
      </SettingsGroup>

      <SettingsGroup title="记忆模型">
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
        <SettingsRow label="记忆模型">
          <Select
            value={deepseekMemoryModel}
            onValueChange={(value) =>
              void setDeepseekMemoryModel(value as DeepseekModel)
            }
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
      </SettingsGroup>

      <SettingsGroup title="DeepSeek 凭据">
        <RuntimeSecretList
          items={secretItems({ names: ["DEEPSEEK_API_KEY"], statuses })}
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
        <RuntimeSecretList
          items={secretItems({ names: ["TAVILY_API_KEY"], statuses })}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>

      <SettingsGroup title="语音通话与语音输入">
        <SettingsRow label="供应商">
          <Badge variant="secondary">火山引擎</Badge>
        </SettingsRow>
        <RuntimeSecretList
          items={secretItems({ names: VOLC_SECRETS, statuses })}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>
    </SettingsPageLayout>
  );
}
