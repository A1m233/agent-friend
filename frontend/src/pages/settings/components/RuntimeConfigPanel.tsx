import { useEffect, useState } from "react";
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
  credentialGroupForModel,
  defaultModelSelection,
  modelProvidersFromCatalog,
  providerModelsFromCatalog,
  useModelCatalogStore,
} from "@/lib/model-config";
import {
  type RuntimeConfigGroup,
  type RuntimeCredentialFieldMutation,
  type RuntimeCredentialGroupSnapshot,
} from "@/lib/runtime-config";
import {
  useSetting,
  type ModelProvider,
  type ModelSelection,
} from "@/lib/settings";
import { type FriendlyError } from "@/services";
import {
  startRuntimeCredentialSync,
  useRuntimeCredentialStore,
} from "@/stores";
import { RuntimeCredentialGroupEditor } from "./RuntimeCredentialGroupEditor";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";
import { SettingsRow } from "./SettingsRow";
import {
  NEW_CONVERSATION_DEFAULT_MODEL_COPY,
  persistModelSettingSelection,
} from "./modelSettingPersistence";

const GROUP_HELP: Record<RuntimeConfigGroup, string> = {
  deepseek: "保存新值时会调用模型列表接口校验，不会生成模型回答。",
  kimi: "保存新值时会调用模型列表接口校验，不会生成模型回答。",
  openrouter: "保存新值时只调用 OpenRouter Key 鉴权接口，不会生成模型回答。",
  tavily: "保存时不发送搜索请求；凭据将在实际使用时检查。",
  amap: "保存时不发送地图请求；凭据将在实际使用时检查。",
  qWeather: "只检查字段完整性，不发送真实天气查询。",
  volcVoice: "本期跳过在线校验，只检查现有必要字段并统一重启语音服务。",
};

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
      <SettingsRow label="调用渠道">
        <Select
          value={selection.provider}
          disabled={disabled}
          onValueChange={(value) => {
            const next = defaultModelSelection(value as ModelProvider, purpose);
            if (next) void onChange(next);
          }}
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

function ProviderRow({ value, label }: { value: string; label: string }) {
  return (
    <SettingsRow label="供应商">
      <Select value={value} disabled>
        <SelectTrigger className="min-w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={value}>{label}</SelectItem>
        </SelectContent>
      </Select>
    </SettingsRow>
  );
}

function CredentialEditor({
  group,
  snapshot,
  apply,
  confirm,
  cancel,
  retry,
  retryValidation,
}: {
  group: RuntimeConfigGroup;
  snapshot?: RuntimeCredentialGroupSnapshot;
  apply: (
    group: RuntimeConfigGroup,
    changes: RuntimeCredentialFieldMutation[],
  ) => Promise<RuntimeCredentialGroupSnapshot>;
  confirm: (operationId: string) => Promise<RuntimeCredentialGroupSnapshot>;
  cancel: (operationId: string) => Promise<RuntimeCredentialGroupSnapshot>;
  retry: (group: RuntimeConfigGroup) => Promise<RuntimeCredentialGroupSnapshot>;
  retryValidation: (
    group: RuntimeConfigGroup,
  ) => Promise<RuntimeCredentialGroupSnapshot>;
}) {
  if (!snapshot) return <div className="text-sm text-muted">正在读取凭据状态…</div>;

  async function report(result: RuntimeCredentialGroupSnapshot) {
    if (result.operation.phase === "applied") {
      toast.success("凭据已保存并应用");
    } else if (result.operation.phase === "applyBlocked") {
      toast.warning("凭据已保存，但尚未应用", {
        description: result.operation.message ?? undefined,
      });
    } else if (result.operation.phase === "failed") {
      toast.error(result.operation.persisted ? "凭据尚未应用" : "凭据未保存", {
        description: result.operation.message ?? undefined,
      });
    }
    return result;
  }

  return (
    <RuntimeCredentialGroupEditor
      snapshot={snapshot}
      validationHelp={GROUP_HELP[group]}
      onApply={async (changes) => report(await apply(group, changes))}
      onConfirm={async (operationId) => report(await confirm(operationId))}
      onCancel={cancel}
      onRetryApply={async () => report(await retry(group))}
      onRetryValidation={async () => {
        const result = await retryValidation(group);
        if (result.validation === "valid") toast.success("当前凭据校验成功");
        else await report(result);
        return result;
      }}
    />
  );
}

export function RuntimeConfigPanel({ focusGroup }: { focusGroup?: RuntimeConfigGroup }) {
  const [chatModel] = useSetting("chatModel");
  const [memoryModel] = useSetting("memoryModel");
  const [savingModel, setSavingModel] = useState<"chatModel" | "memoryModel" | null>(null);
  const groups = useRuntimeCredentialStore((state) => state.groups);
  const loading = useRuntimeCredentialStore((state) => state.loading);
  const error = useRuntimeCredentialStore((state) => state.error);
  const refresh = useRuntimeCredentialStore((state) => state.refresh);
  const applyGroup = useRuntimeCredentialStore((state) => state.applyGroup);
  const confirmSave = useRuntimeCredentialStore((state) => state.confirmSave);
  const cancelOperation = useRuntimeCredentialStore((state) => state.cancelOperation);
  const retryApply = useRuntimeCredentialStore((state) => state.retryApply);
  const retryValidation = useRuntimeCredentialStore((state) => state.retryValidation);
  const loadModelCatalog = useModelCatalogStore((state) => state.load);

  useEffect(() => startRuntimeCredentialSync(), []);
  useEffect(
    () => () => {
      for (const item of useRuntimeCredentialStore.getState().groups) {
        const operationId = item.operation.operationId;
        if (item.operation.phase === "awaitingConfirmation" && operationId) {
          void useRuntimeCredentialStore
            .getState()
            .cancelOperation(operationId)
            .catch(() => undefined);
        }
      }
    },
    [],
  );
  useEffect(() => {
    void loadModelCatalog();
  }, [loadModelCatalog]);
  useEffect(() => {
    if (!focusGroup) return;
    window.setTimeout(() => {
      document
        .getElementById(`runtime-credential-${focusGroup}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  }, [focusGroup]);

  const group = (id: RuntimeConfigGroup) => groups.find((item) => item.group === id);

  async function saveModelSetting(
    key: "chatModel" | "memoryModel",
    selection: ModelSelection,
  ): Promise<void> {
    setSavingModel(key);
    try {
      const result = await persistModelSettingSelection(key, selection);
      if (result.scope === "newConversationDraft") {
        toast.success(NEW_CONVERSATION_DEFAULT_MODEL_COPY.successTitle, {
          description: NEW_CONVERSATION_DEFAULT_MODEL_COPY.successDescription,
        });
        return;
      }
      if (result.applied) {
        toast.success("记忆模型已保存并生效", { description: "对话服务已自动重启" });
      } else {
        toast.warning("记忆模型已保存，但尚未应用", {
          description: result.applyError ?? undefined,
        });
      }
    } catch (saveError) {
      const friendly = saveError as Partial<FriendlyError>;
      const credentialGroup = credentialGroupForModel(selection.model);
      toast.error(key === "chatModel"
        ? NEW_CONVERSATION_DEFAULT_MODEL_COPY.errorTitle
        : "记忆模型未保存", {
        description: friendly.friendly ?? String(saveError),
        action:
          friendly.code === "credential_missing" && credentialGroup
            ? {
                label: "配置凭据",
                onClick: () =>
                  document
                    .getElementById(`runtime-credential-${credentialGroup}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "center" }),
              }
            : undefined,
      });
    } finally {
      setSavingModel(null);
    }
  }

  const editorProps = {
    apply: applyGroup,
    confirm: confirmSave,
    cancel: cancelOperation,
    retry: retryApply,
    retryValidation,
  };

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
      <SettingsGroup
        title={NEW_CONVERSATION_DEFAULT_MODEL_COPY.groupTitle}
        description={NEW_CONVERSATION_DEFAULT_MODEL_COPY.groupDescription}
      >
        <ModelSelectionRows
          selection={chatModel}
          purpose="chat"
          modelLabel={NEW_CONVERSATION_DEFAULT_MODEL_COPY.modelLabel}
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

      <SettingsGroup id="runtime-credential-deepseek" title="DeepSeek 凭据">
        <CredentialEditor group="deepseek" snapshot={group("deepseek")} {...editorProps} />
      </SettingsGroup>
      <SettingsGroup id="runtime-credential-kimi" title="Kimi 凭据">
        <CredentialEditor group="kimi" snapshot={group("kimi")} {...editorProps} />
      </SettingsGroup>
      <SettingsGroup id="runtime-credential-openrouter" title="OpenRouter 凭据">
        <CredentialEditor
          group="openrouter"
          snapshot={group("openrouter")}
          {...editorProps}
        />
      </SettingsGroup>

      {error ? <div className="text-sm text-danger">{error}</div> : null}

      <SettingsGroup id="runtime-credential-tavily" title="Web Search">
        <ProviderRow value="tavily" label="Tavily" />
        <CredentialEditor group="tavily" snapshot={group("tavily")} {...editorProps} />
      </SettingsGroup>

      <SettingsGroup id="runtime-credential-amap" title="地图与地点">
        <ProviderRow value="amap" label="高德地图" />
        <CredentialEditor group="amap" snapshot={group("amap")} {...editorProps} />
      </SettingsGroup>

      <SettingsGroup id="runtime-credential-qWeather" title="天气">
        <ProviderRow value="qweather" label="和风天气" />
        <CredentialEditor group="qWeather" snapshot={group("qWeather")} {...editorProps} />
      </SettingsGroup>

      <SettingsGroup id="runtime-credential-volcVoice" title="语音通话与语音输入">
        <SettingsRow label="供应商">
          <Badge variant="secondary">火山引擎</Badge>
        </SettingsRow>
        <CredentialEditor group="volcVoice" snapshot={group("volcVoice")} {...editorProps} />
      </SettingsGroup>
    </SettingsPageLayout>
  );
}
