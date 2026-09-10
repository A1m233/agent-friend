import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  Button,
  Input,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui";
import {
  useSetting,
  type CustomConnection,
  type ModelSelection,
} from "@/lib/settings";
import { useModelCatalogStore } from "@/lib/model-config";
import {
  saveConnections,
  retryConnections,
} from "@/lib/model-config/connections";
import { SettingsGroup } from "./SettingsGroup";
import {
  ConnectionModelPicker,
  inspectionError,
} from "./ConnectionModelPicker";

function newConnection(): CustomConnection {
  return {
    id: crypto.randomUUID(),
    name: "",
    baseUrl: "",
    protocol: "chat_completions",
    credentialRevision: crypto.randomUUID(),
    models: [],
  };
}

function explain(error: unknown): string {
  const code = String(error);
  if (code.includes("connection_in_use"))
    return "此连接仍有对话或记忆任务运行。请等待结束或停止任务后重试。";
  if (code.includes("settings_changed"))
    return "设置已被其他窗口修改，请关闭后重新编辑。";
  if (code.includes("api_key_required")) return "请填写 API Key。";
  if (code.includes("bridge_not_ready"))
    return "对话服务尚未就绪，请稍后重试。";
  if (code.includes("invalid_connection_configuration"))
    return "请检查地址、模型名、上下文长度和默认模型；不能删除仍被默认用途引用的模型。";
  return inspectionError(code.replace(/^Error: /, ""));
}

export function CustomConnections() {
  const [settings] = useSetting("modelConnections");
  const [chatModel] = useSetting("chatModel");
  const [memoryModel] = useSetting("memoryModel");
  const catalog = useModelCatalogStore((state) => state.catalog);
  const [draft, setDraft] = useState<CustomConnection | null>(null);
  const [revision, setRevision] = useState(0);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [defaults, setDefaults] = useState({ chatModel, memoryModel });
  const [error, setError] = useState<string | null>(null);
  const [inspectionVersion, setInspectionVersion] = useState(0);
  const automaticContext = useRef<Record<string, number>>({});
  const connections = settings?.connections ?? [];

  function edit(connection: CustomConnection, remove = false) {
    setDraft(structuredClone(connection));
    setRevision(settings?.revision ?? 0);
    setKey("");
    setError(null);
    setInspectionVersion((value) => value + 1);
    automaticContext.current = {};
    setDeleting(remove);
    setDefaults({ chatModel, memoryModel });
  }
  function changeConnection(
    change: Partial<CustomConnection>,
    nextKey?: string,
  ) {
    if (!draft) return;
    setDraft({
      ...draft,
      ...change,
      models: draft.models.map((m) =>
        automaticContext.current[m.id] === m.contextWindow
          ? { ...m, contextWindow: 0 }
          : m,
      ),
    });
    automaticContext.current = {};
    if (nextKey !== undefined) setKey(nextKey);
    setInspectionVersion((value) => value + 1);
    setError(null);
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(explain(e));
      toast.error("操作未完成", { description: explain(e) });
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!draft) return;
    await perform(async () => {
      const next = {
        ...draft,
        name: draft.name.trim(),
        baseUrl: draft.baseUrl.trim(),
        models: draft.models.map((m) => ({ ...m, name: m.name.trim() })),
        credentialRevision: key.trim()
          ? crypto.randomUUID()
          : draft.credentialRevision,
      };
      const remaining = connections.filter((item) => item.id !== draft.id);
      const result = await saveConnections(
        revision,
        deleting ? remaining : [...remaining, next],
        key.trim() && !deleting ? { [draft.id]: key } : {},
        deleting ? defaults : undefined,
      );
      setKey("");
      setDraft(null);
      setPending(!result.applied || result.cleanupComplete === false);
      if (result.cleanupComplete === false) {
        toast.warning("连接设置已保存，旧凭据清理待重试");
      } else {
        toast.success(
          result.applied ? "连接设置已保存，下次执行生效" : "已保存，等待应用",
        );
      }
    });
  }
  const alternatives =
    catalog?.providers.flatMap((p) =>
      p.models
        .filter((m) => !draft || !m.id.startsWith(`custom/${draft.id}/`))
        .map((m) => ({
          value: { provider: p.id, model: m.id } as ModelSelection,
          label: m.label,
        })),
    ) ?? [];

  return (
    <SettingsGroup
      title="自定义连接"
      description="连接服务后获取模型列表，选择模型并测试，再保存。支持 OpenAI 兼容接口。"
    >
      <div className="space-y-3">
        {connections.map((connection) => (
          <div
            key={connection.id}
            className="flex items-center justify-between gap-3 border-b border-border py-3"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">{connection.name}</p>
              <p className="text-xs text-muted">
                {connection.protocol === "responses"
                  ? "Responses"
                  : "Chat Completions"}{" "}
                · {connection.models.length} 个模型
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => edit(connection)}
              >
                编辑
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={busy}
                onClick={() => edit(connection, true)}
              >
                删除
              </Button>
            </div>
          </div>
        ))}
        <Button
          variant="outline"
          disabled={busy || pending}
          onClick={() => edit(newConnection())}
        >
          添加自定义连接
        </Button>
        {(pending || error) && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                await retryConnections();
                setPending(false);
              })
            }
          >
            重试应用
          </Button>
        )}
        {!draft && error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </div>
      <Dialog
        open={draft !== null}
        onOpenChange={(open) => {
          if (!open && !busy) {
            setDraft(null);
            setKey("");
          }
        }}
      >
        <DialogContent scrollMode="overlay">
          <DialogHeader>
            <DialogTitle>
              {deleting ? "删除连接" : "自定义模型连接"}
            </DialogTitle>
            <DialogDescription>
              {deleting
                ? "历史记录会保留。请先替换此连接承担的默认用途；运行中的任务结束后才能删除。"
                : "先填写连接信息，再获取并选择模型。保存不生成回答，模型测试由你单独触发。"}
            </DialogDescription>
          </DialogHeader>
          {draft &&
            (deleting ? (
              <div className="space-y-3">
                {(["chatModel", "memoryModel"] as const)
                  .filter((purpose) =>
                    defaults[purpose].model.startsWith(`custom/${draft.id}/`),
                  )
                  .map((purpose) => (
                    <div key={purpose} className="space-y-2">
                      <label className="text-sm font-medium">
                        {purpose === "chatModel"
                          ? "替换默认对话模型"
                          : "替换记忆模型"}
                      </label>
                      <Select
                        value={defaults[purpose].model}
                        onValueChange={(value) => {
                          const found = alternatives.find(
                            (m) => m.value.model === value,
                          );
                          if (found)
                            setDefaults((previous) => ({
                              ...previous,
                              [purpose]: found.value,
                            }));
                        }}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="选择替代模型" />
                        </SelectTrigger>
                        <SelectContent>
                          {alternatives.map((m) => (
                            <SelectItem
                              key={m.value.model}
                              value={m.value.model}
                            >
                              {m.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  ))}
                <p className="text-sm">将删除：{draft.name}</p>
              </div>
            ) : (
              <fieldset disabled={busy} className="space-y-3">
                <div className="space-y-2">
                  <label
                    className="text-sm font-medium"
                    htmlFor="connection-name"
                  >
                    连接名称
                  </label>
                  <Input
                    id="connection-name"
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <label
                    className="text-sm font-medium"
                    htmlFor="connection-url"
                  >
                    Base URL
                  </label>
                  <Input
                    id="connection-url"
                    placeholder="https://example.com/v1"
                    value={draft.baseUrl}
                    onChange={(e) =>
                      changeConnection({ baseUrl: e.target.value })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">接口协议</label>
                  <Select
                    value={draft.protocol}
                    onValueChange={(value) =>
                      changeConnection({
                        protocol: value as CustomConnection["protocol"],
                      })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="chat_completions">
                        Chat Completions
                      </SelectItem>
                      <SelectItem value="responses">Responses</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <label
                    className="text-sm font-medium"
                    htmlFor="connection-key"
                  >
                    API Key
                  </label>
                  <Input
                    id="connection-key"
                    type="password"
                    autoComplete="new-password"
                    value={key}
                    placeholder={
                      connections.some((c) => c.id === draft.id)
                        ? "留空保留已保存的密钥"
                        : "填写密钥"
                    }
                    onChange={(e) => changeConnection({}, e.target.value)}
                  />
                </div>
                <ConnectionModelPicker
                  key={`${draft.id}-${inspectionVersion}`}
                  draft={draft}
                  apiKey={key}
                  busy={busy}
                  hasSavedKey={connections.some((c) => c.id === draft.id)}
                  perform={perform}
                  onChange={(models) => setDraft({ ...draft, models })}
                  onAutomaticContext={(id, value) => {
                    automaticContext.current[id] = value;
                  }}
                  onManualContext={(id) => {
                    delete automaticContext.current[id];
                  }}
                />
              </fieldset>
            ))}
          <p
            role={error && !busy ? "alert" : "status"}
            className={`min-h-5 text-sm ${error && !busy ? "text-danger" : "text-muted"}`}
          >
            {busy ? "正在处理，请稍候…" : error}
          </p>
          <DialogFooter>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setDraft(null);
                setKey("");
              }}
            >
              取消
            </Button>
            <Button
              variant={deleting ? "destructive" : "default"}
              disabled={
                busy ||
                (!deleting &&
                  (!draft?.name.trim() ||
                    !draft.baseUrl.trim() ||
                    (!key.trim() &&
                      !connections.some((c) => c.id === draft.id)) ||
                    draft.models.length === 0 ||
                    draft.models.some(
                      (m) =>
                        !m.name.trim() ||
                        !Number.isInteger(m.contextWindow) ||
                        m.contextWindow <= 0,
                    ))) ||
                (deleting &&
                  Object.values(defaults).some((v) =>
                    v.model.startsWith(`custom/${draft?.id}/`),
                  ))
              }
              loading={busy}
              onClick={() => void save()}
            >
              {deleting ? "删除连接" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsGroup>
  );
}
