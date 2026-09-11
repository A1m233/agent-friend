import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ReasoningSelection } from "@/components/model-config/ReasoningSelection";
import { RefreshCw } from "lucide-react";
import {
  Button,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
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
  runtimeCredentialOperationBusy,
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
import { RuntimeCredentialSection } from "./RuntimeCredentialSection";
import { CustomConnections } from "./CustomConnections";
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
  const reasoning = models.find((model) => model.id === selection.model)?.reasoning;

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
            <SelectValue>{models.find((model) => model.id === selection.model)?.label ?? selection.model}</SelectValue>
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
      {reasoning && <SettingsRow label="推理强度"><ReasoningSelection definition={reasoning} value={selection.reasoningPreset ?? reasoning.defaultPreset} disabled={disabled} onChange={(reasoningPreset) => void onChange({ ...selection, reasoningPreset })} /></SettingsRow>}
    </>
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

const MODEL_CREDENTIALS = [
  { id: "deepseek", title: "DeepSeek", description: "DeepSeek 官方模型" },
  { id: "kimi", title: "Kimi", description: "Moonshot AI 模型" },
] as const;
const SERVICE_CREDENTIALS = [
  { id: "tavily", title: "网络搜索", description: "Tavily" },
  { id: "amap", title: "地图与地点", description: "高德地图" },
  { id: "qWeather", title: "天气", description: "和风天气" },
  { id: "volcVoice", title: "语音通话与语音输入", description: "火山引擎" },
] as const;
type CredentialTab = "models" | "services";
function tabForGroup(group?: RuntimeConfigGroup): CredentialTab {
  return SERVICE_CREDENTIALS.some((item) => item.id === group) ? "services" : "models";
}

export function RuntimeConfigPanel({ focusGroup }: { focusGroup?: RuntimeConfigGroup }) {
  const [tab, setTab] = useState<CredentialTab>(() => tabForGroup(focusGroup));
  const [expanded, setExpanded] = useState<Partial<Record<RuntimeConfigGroup, boolean>>>(
    () => focusGroup ? { [focusGroup]: true } : {},
  );
  const [scrollGroup, setScrollGroup] = useState<RuntimeConfigGroup>();
  const revealGroup = useCallback((id: RuntimeConfigGroup) => {
    setTab(tabForGroup(id));
    setExpanded((current) => ({ ...current, [id]: true }));
    setScrollGroup(id);
  }, []);
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
    if (focusGroup) revealGroup(focusGroup);
  }, [focusGroup, revealGroup]);
  useEffect(() => {
    if (!scrollGroup) return;
    const timer = window.setTimeout(() => {
      document.getElementById(`runtime-credential-${scrollGroup}`)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setScrollGroup(undefined);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [scrollGroup]);

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
        toast.success("记忆模型已保存", { description: "下一次记忆任务使用新配置，正在运行的任务保持原配置。" });
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
                onClick: () => revealGroup(credentialGroup),
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

  const busy = savingModel !== null || groups.some((item) =>
    runtimeCredentialOperationBusy(item.operation) || item.operation.phase === "awaitingConfirmation",
  );
  const renderCredentials = (items: readonly { id: RuntimeConfigGroup; title: string; description: string }[]) =>
    items.map((item) => (
      <RuntimeCredentialSection key={item.id} id={`runtime-credential-${item.id}`}
        title={item.title} description={item.description} snapshot={group(item.id)}
        open={expanded[item.id] ?? false}
        onOpenChange={(open) => setExpanded((current) => ({ ...current, [item.id]: open }))}>
        <CredentialEditor group={item.id} snapshot={group(item.id)} {...editorProps} />
      </RuntimeCredentialSection>
    ));

  return (
    <SettingsPageLayout title="模型与凭据" actions={
      <Button type="button" variant="ghost" size="sm" onClick={() => void refresh()}
        loading={loading} disabled={loading || busy}>
        {!loading && <RefreshCw className="size-4" />}刷新
      </Button>
    }>
      <p className="text-sm text-muted">选择使用的模型，管理模型和服务的访问凭据。</p>
      {error ? <div role="alert" className="text-sm text-danger">{error}</div> : null}
      <Tabs value={tab} onValueChange={(value) => setTab(value as CredentialTab)} className="gap-4">
        <TabsList aria-label="模型与凭据分类">
          <TabsTrigger value="models" disabled={busy}>模型</TabsTrigger>
          <TabsTrigger value="services" disabled={busy}>服务凭据</TabsTrigger>
        </TabsList>
        <TabsContent value="models" forceMount hidden={tab !== "models"} className="space-y-4 data-[state=inactive]:hidden">
          <SettingsGroup title="默认模型" description="为对话和记忆处理分别选择模型。">
            <div className="space-y-2">
              <h4 className="text-sm font-medium">{NEW_CONVERSATION_DEFAULT_MODEL_COPY.groupTitle}</h4>
              <p className="text-xs leading-relaxed text-muted">{NEW_CONVERSATION_DEFAULT_MODEL_COPY.groupDescription}</p>
              <ModelSelectionRows selection={chatModel} purpose="chat"
                modelLabel={NEW_CONVERSATION_DEFAULT_MODEL_COPY.modelLabel}
                onChange={(selection) => saveModelSetting("chatModel", selection)} disabled={busy} />
            </div>
            <div className="space-y-2 border-t border-border pt-4">
              <h4 className="text-sm font-medium">记忆模型</h4>
              <ModelSelectionRows selection={memoryModel} purpose="memory" modelLabel="记忆模型"
                onChange={(selection) => saveModelSetting("memoryModel", selection)} disabled={busy} />
            </div>
          </SettingsGroup>
          <SettingsGroup title="模型供应商" description="配置对应供应商的 API Key，供模型调用使用。">
            <div>{renderCredentials(MODEL_CREDENTIALS)}</div>
          </SettingsGroup>
          {/* OpenRouter 暂不维护：保留存量设置及后端兼容，不展示编辑入口。 */}
          <CustomConnections />
        </TabsContent>
        <TabsContent value="services" forceMount hidden={tab !== "services"} className="space-y-4 data-[state=inactive]:hidden">
          <SettingsGroup title="服务凭据" description="按需配置搜索、地图、天气和语音服务。">
            <div>{renderCredentials(SERVICE_CREDENTIALS)}</div>
          </SettingsGroup>
        </TabsContent>
      </Tabs>
    </SettingsPageLayout>
  );
}
