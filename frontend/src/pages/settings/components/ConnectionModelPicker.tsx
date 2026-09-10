import { useState } from "react";
import { Plus, Trash2, ChevronDown } from "lucide-react";
import {
  Button,
  Input,
  Checkbox,
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui";
import type { CustomConnection } from "@/lib/settings";
import {
  discoverModels,
  probeModel,
  readModelMetadata,
  type DiscoveredModel,
  type ProbeResult,
} from "@/lib/model-config/connections";

export const inspectionError = (code: string) =>
  ({
    authentication_failed: "鉴权失败，请检查 API Key",
    permission_denied: "当前密钥没有访问权限",
    billing_required: "账户额度不足",
    rate_limited: "服务限流，请稍后重试",
    request_timeout: "请求超时，请稍后重试",
    network_unavailable: "无法连接服务，请检查地址或代理",
    protocol_or_model_rejected: "服务拒绝此协议或模型，请检查配置",
    model_list_unavailable: "服务不提供模型列表，可手动添加",
    upstream_unavailable: "上游服务暂不可用",
    invalid_response: "响应不符合预期",
    model_unavailable: "模型不可用",
  })[code] ?? "操作未完成，请检查连接配置";

interface Props {
  draft: CustomConnection;
  apiKey: string;
  busy: boolean;
  hasSavedKey: boolean;
  onChange: (models: CustomConnection["models"]) => void;
  onAutomaticContext: (id: string, value: number) => void;
  onManualContext: (id: string) => void;
  perform: (action: () => Promise<void>) => Promise<void>;
}

export function ConnectionModelPicker({
  draft,
  apiKey,
  busy,
  hasSavedKey,
  onChange,
  onAutomaticContext,
  onManualContext,
  perform,
}: Props) {
  const [models, setModels] = useState<DiscoveredModel[]>([]);
  const [fetched, setFetched] = useState(false);
  const [query, setQuery] = useState("");
  const [manual, setManual] = useState(false);
  const [name, setName] = useState("");
  const [hints, setHints] = useState<Record<string, DiscoveredModel>>({});
  const [probes, setProbes] = useState<Record<string, ProbeResult>>({});
  const [operation, setOperation] = useState<string | null>(null);
  const ready = !!draft.baseUrl.trim() && (!!apiKey.trim() || hasSavedKey);
  const selected = new Set(draft.models.map((m) => m.name));

  async function run(id: string, action: () => Promise<void>) {
    setOperation(id);
    try {
      await perform(action);
    } finally {
      setOperation(null);
    }
  }
  function add(option: DiscoveredModel) {
    if (selected.has(option.id)) return;
    const id = crypto.randomUUID();
    if (option.contextWindow) onAutomaticContext(id, option.contextWindow);
    setHints((previous) => ({ ...previous, [option.id]: option }));
    onChange([
      ...draft.models,
      { id, name: option.id, contextWindow: option.contextWindow ?? 0 },
    ]);
  }
  function remove(id: string) {
    onChange(draft.models.filter((m) => m.id !== id));
  }

  return (
    <div className="space-y-4 border-t border-border pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">选择模型</h3>
        <Button
          disabled={busy || !ready}
          loading={operation === "discover"}
          onClick={() =>
            void run("discover", async () => {
              const found = await discoverModels(draft, apiKey);
              setModels(found);
              setFetched(true);
              setHints((previous) => ({
                ...previous,
                ...Object.fromEntries(found.map((m) => [m.id, m])),
              }));
              onChange(
                draft.models.map((m) => {
                  const info = found.find((item) => item.id === m.name);
                  if (m.contextWindow > 0 || !info?.contextWindow) return m;
                  onAutomaticContext(m.id, info.contextWindow);
                  return { ...m, contextWindow: info.contextWindow };
                }),
              );
            })
          }
        >
          获取模型列表
        </Button>
      </div>
      <p className="text-xs text-muted">
        填好地址和密钥即可获取，无需先保存，也不会生成回答。
      </p>
      {fetched && (
        <div className="space-y-2">
          <p className="text-xs text-muted" aria-live="polite">
            已获取 {models.length} 个模型，可搜索并勾选添加。
          </p>
          <Input
            aria-label="搜索模型"
            placeholder="搜索模型名称"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div
            className="max-h-64 space-y-1 overflow-y-auto rounded-md border border-border p-2"
            aria-label="可用模型"
          >
            {models
              .filter((m) => m.id.toLowerCase().includes(query.toLowerCase()))
              .map((m) => (
                <label
                  key={m.id}
                  className="flex cursor-pointer items-start gap-3 rounded-md p-2 hover:bg-surface"
                >
                  <Checkbox
                    disabled={busy}
                    checked={selected.has(m.id)}
                    onCheckedChange={(checked) =>
                      checked
                        ? add(m)
                        : onChange(
                            draft.models.filter((item) => item.name !== m.id),
                          )
                    }
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block break-all text-sm">{m.id}</span>
                    <span className="text-xs text-muted">
                      {m.contextWindow
                        ? `${m.contextWindow.toLocaleString()} tokens · ${m.contextLabel}`
                        : "上下文未知，添加后补充"}
                    </span>
                  </span>
                  {selected.has(m.id) && (
                    <span className="text-xs text-muted">已添加</span>
                  )}
                </label>
              ))}
            {!models.some((m) =>
              m.id.toLowerCase().includes(query.toLowerCase()),
            ) && (
              <p className="p-2 text-sm text-muted">
                没有匹配模型，可以手动添加。
              </p>
            )}
          </div>
        </div>
      )}
      <Collapsible open={manual} onOpenChange={setManual}>
        <CollapsibleTrigger asChild>
          <Button variant="outline" size="sm" disabled={busy}>
            <Plus />
            手动添加模型
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-2 pt-3">
          <Input
            aria-label="Model Name"
            placeholder="服务提供的完整 Model Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={busy || !name.trim() || selected.has(name.trim())}
            loading={operation === "manual"}
            onClick={() =>
              void run("manual", async () => {
                const value = name.trim();
                const info = await readModelMetadata(value).catch(() => ({
                  id: value,
                  contextWindow: null,
                  contextSource: null,
                  contextLabel: "未获取到上下文信息",
                }));
                add(info);
                setName("");
                setManual(false);
              })
            }
          >
            <Plus />
            添加此模型
          </Button>
        </CollapsibleContent>
      </Collapsible>
      <h3 className="text-sm font-semibold">
        已添加模型 · {draft.models.length}
      </h3>
      {draft.models.length === 0 && (
        <p className="text-sm text-muted">
          从列表选择模型，或手动添加。至少添加一个模型后才能保存。
        </p>
      )}
      {draft.models.map((model) => (
        <div
          key={model.id}
          className="space-y-3 rounded-md border border-border p-3"
        >
          <div className="flex items-start justify-between gap-2">
            <p className="min-w-0 break-all text-sm font-medium">
              {model.name}
            </p>
            <Button
              variant="destructive"
              size="sm"
              disabled={busy}
              aria-label={`移除模型 ${model.name}`}
              onClick={() => remove(model.id)}
            >
              <Trash2 />
              移除
            </Button>
          </div>
          <p className="text-xs text-muted">
            {model.contextWindow > 0
              ? `${model.contextWindow.toLocaleString()} tokens · ${hints[model.name]?.contextWindow === model.contextWindow ? hints[model.name].contextLabel : "已保存或手动配置"}`
              : "服务和目录未提供上下文信息，请在下面补充；仍可先测试模型。"}
          </p>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2"><Checkbox disabled={busy} checked={model.images ?? false} onCheckedChange={(checked) => onChange(draft.models.map((m) => m.id === model.id ? { ...m, images: checked === true } : m))} />图片输入</label>
            <label className="flex items-center gap-2"><Checkbox disabled={busy} checked={model.documents ?? false} onCheckedChange={(checked) => onChange(draft.models.map((m) => m.id === model.id ? { ...m, documents: checked === true } : m))} />文档输入（PDF / TXT）</label>
          </div>
          <p className="text-xs text-muted">按服务能力启用。图片与 PDF 使用 OpenAI 内容格式；TXT 发送文本。服务拒绝时会报错，不切换其他模型。</p>
          <Collapsible defaultOpen={model.contextWindow <= 0}>
            <CollapsibleTrigger asChild>
              <Button variant="outline" size="sm" disabled={busy}>
                <ChevronDown />
                上下文设置
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-2 pt-3">
              <label className="text-sm" htmlFor={`context-${model.id}`}>
                上下文预算（tokens）
              </label>
              <Input
                id={`context-${model.id}`}
                type="number"
                min={1}
                value={model.contextWindow || ""}
                onChange={(e) => {
                  onManualContext(model.id);
                  setHints((previous) => ({
                    ...previous,
                    [model.name]: {
                      id: model.name,
                      contextWindow: Number(e.target.value),
                      contextSource: null,
                      contextLabel: "手动覆盖",
                    },
                  }));
                  onChange(
                    draft.models.map((m) =>
                      m.id === model.id
                        ? { ...m, contextWindow: Number(e.target.value) }
                        : m,
                    ),
                  );
                }}
              />
              <p className="text-xs text-muted">
                用于控制历史压缩。目录数值是参考，可按服务实际限制覆盖；模型测试不会测量最大上下文。
              </p>
            </CollapsibleContent>
          </Collapsible>
          <Button
            variant="outline"
            size="sm"
            disabled={busy || !ready}
            loading={operation === model.id}
            onClick={() =>
              void run(model.id, async () => {
                const result = await probeModel(draft, apiKey, model.name);
                setProbes((previous) => ({ ...previous, [model.id]: result }));
                if (model.contextWindow <= 0 && result.metadata.contextWindow) {
                  setHints((previous) => ({
                    ...previous,
                    [model.name]: result.metadata,
                  }));
                  onAutomaticContext(model.id, result.metadata.contextWindow);
                  onChange(
                    draft.models.map((m) =>
                      m.id === model.id
                        ? {
                            ...m,
                            contextWindow: result.metadata.contextWindow!,
                          }
                        : m,
                    ),
                  );
                }
              })
            }
          >
            测试模型
          </Button>
          {probes[model.id] && (
            <div role="status" className="space-y-1 text-sm">
              {(["generation", "tools"] as const).map((kind) => {
                const result = probes[model.id][kind];
                return (
                  <p
                    key={kind}
                    className={
                      result.status === "failed" ? "text-danger" : "text-muted"
                    }
                  >
                    {kind === "generation" ? "模型生成" : "工具调用与回传"}：
                    {result.status === "passed"
                      ? "通过"
                      : result.status === "skipped"
                        ? "未执行"
                        : inspectionError(result.code ?? "")}
                  </p>
                );
              })}
            </div>
          )}
        </div>
      ))}
      <p className="text-xs text-muted">
        测试使用当前草稿，最多发送 3
        次小请求。修改地址、密钥或协议后，需重新获取列表和测试。
      </p>
    </div>
  );
}
