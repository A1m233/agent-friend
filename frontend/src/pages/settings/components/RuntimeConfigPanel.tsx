import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
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
import {
  defaultModelSelection,
  modelProvidersFromCatalog,
  providerModelsFromCatalog,
  useModelCatalogStore,
} from "@/lib/model-config";
import {
  describeRuntimeConfigEffect,
  persistBridgeRuntimeSetting,
  type RuntimeConfigItemStatus,
  type RuntimeConfigSource,
  type RuntimeSecretName,
  useRuntimeConfig,
} from "@/lib/runtime-config";
import {
  setSetting,
  useSetting,
  type ModelProvider,
  type ModelSelection,
} from "@/lib/settings";
import {
  RuntimeSecretList,
  type RuntimeSecretListItem,
} from "./RuntimeSecretList";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";
import { SettingsRow } from "./SettingsRow";

const SECRET_LABELS: Record<RuntimeSecretName, string> = {
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

function ModelSelectionRows({
  selection,
  purpose,
  modelLabel,
  onChange,
  disabled,
}: {
  selection: ModelSelection;
  purpose: "chat" | "memory";
  modelLabel: string;
  onChange: (next: ModelSelection) => Promise<void>;
  disabled?: boolean;
}) {
  const catalog = useModelCatalogStore((state) => state.catalog);
  const providers = modelProvidersFromCatalog(catalog);
  const models = providerModelsFromCatalog(catalog, selection.provider);

  return (
    <>
      <SettingsRow label="供应商">
        <Select
          value={selection.provider}
          disabled={disabled}
          onValueChange={(value) =>
            void (() => {
              const next = defaultModelSelection(value as ModelProvider, purpose);
              return next ? onChange(next) : Promise.resolve();
            })()
          }
        >
          <SelectTrigger className="min-w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {providers.map((provider) => (
              <SelectItem key={provider.id} value={provider.id}>
                {provider.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsRow>
      <SettingsRow label={modelLabel}>
        <Select
          value={selection.model}
          disabled={disabled}
          onValueChange={(value) =>
            void onChange({ provider: selection.provider, model: value })
          }
        >
          <SelectTrigger className="min-w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {models.map((model) => (
              <SelectItem key={model.id} value={model.id}>
                {model.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsRow>
    </>
  );
}

export function RuntimeConfigPanel() {
  const [chatModel] = useSetting("chatModel");
  const [memoryModel] = useSetting("memoryModel");
  const [savingModel, setSavingModel] = useState<"chatModel" | "memoryModel" | null>(null);
  const {
    snapshot,
    loading,
    savingName,
    error,
    refresh,
    saveSecret,
    removeSecret,
  } = useRuntimeConfig();
  const loadModelCatalog = useModelCatalogStore((state) => state.load);

  useEffect(() => {
    void loadModelCatalog();
  }, [loadModelCatalog]);

  const statuses = useMemo(
    () => new Map(snapshot.items.map((item) => [item.name, item] as const)),
    [snapshot.items],
  );

  async function saveModelSetting(
    key: "chatModel" | "memoryModel",
    selection: ModelSelection,
  ): Promise<void> {
    setSavingModel(key);
    try {
      const result = await persistBridgeRuntimeSetting(() =>
        setSetting(key, selection),
      );
      if (result.applied) {
        toast.success("模型设置已保存并生效", {
          description: "对话服务已自动重启",
        });
      } else {
        toast.warning("模型设置已保存，但尚未应用", {
          description: result.applyError ?? undefined,
        });
      }
    } catch (error) {
      toast.error("模型设置保存失败", { description: String(error) });
    } finally {
      setSavingModel(null);
    }
  }

  return (
    <SettingsPageLayout
      title="模型与凭据"
      actions={
        <Button
          type="button"
          variant="outline"
          size="sm"
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
        <ModelSelectionRows
          selection={chatModel}
          purpose="chat"
          modelLabel="主对话模型"
          onChange={(selection) => saveModelSetting("chatModel", selection)}
          disabled={savingModel !== null}
        />
      </SettingsGroup>

      <SettingsGroup title="记忆模型">
        <ModelSelectionRows
          selection={memoryModel}
          purpose="memory"
          modelLabel="记忆模型"
          onChange={(selection) => saveModelSetting("memoryModel", selection)}
          disabled={savingModel !== null}
        />
      </SettingsGroup>

      <SettingsGroup title="DeepSeek 凭据">
        <RuntimeSecretList
          items={secretItems({ names: ["DEEPSEEK_API_KEY"], statuses })}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>

      <SettingsGroup title="Kimi 凭据">
        <RuntimeSecretList
          items={secretItems({ names: ["KIMI_API_KEY"], statuses })}
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

      <SettingsGroup title="地图与地点">
        <SettingsRow label="供应商">
          <Select value="amap" disabled>
            <SelectTrigger className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="amap">高德地图</SelectItem>
            </SelectContent>
          </Select>
        </SettingsRow>
        <RuntimeSecretList
          items={secretItems({ names: ["AMAP_WEBSERVICE_KEY"], statuses })}
          savingName={savingName}
          onSave={saveSecret}
          onDelete={removeSecret}
        />
      </SettingsGroup>

      <SettingsGroup title="天气">
        <SettingsRow label="供应商">
          <Select value="qweather" disabled>
            <SelectTrigger className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="qweather">和风天气</SelectItem>
            </SelectContent>
          </Select>
        </SettingsRow>
        <RuntimeSecretList
          items={secretItems({
            names: ["QWEATHER_API_KEY", "QWEATHER_API_HOST"],
            statuses,
          })}
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
