import { useState } from "react";
import {
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { runtimeCredentialOperationBusy, type RuntimeSecretName } from "@/lib/runtime-config";
import { useRuntimeCredentialStore } from "@/stores";

const fields = {
  key: [["VOLC_SPEECH_API_KEY", "语音 APP Key"]],
  legacy: [
    ["VOLC_SPEECH_APP_ID", "语音 APP ID"],
    ["VOLC_SPEECH_ACCESS_TOKEN", "Access Token"],
  ],
} as const;

export function SpeechInput() {
  const group = useRuntimeCredentialStore((s) => s.groups.find((g) => g.group === "volcVoice"));
  const [chosenMode, setMode] = useState<keyof typeof fields>();
  const [draft, setDraft] = useState<Partial<Record<RuntimeSecretName, string>>>({});
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const stored = (name: RuntimeSecretName) => group?.items.some((i) => i.name === name && i.configured);
  const mode =
    chosenMode ?? (!stored("VOLC_SPEECH_API_KEY") && stored("VOLC_SPEECH_APP_ID") ? "legacy" : "key");
  const changes = fields[mode].flatMap(([name]) =>
    draft[name]?.trim() ? [{ name, action: "set" as const, value: draft[name]!.trim() }] : [],
  );
  const busy = saving || !group || runtimeCredentialOperationBusy(group.operation);
  async function save(retry = false) {
    setSaving(true);
    try {
      const store = useRuntimeCredentialStore.getState();
      const updated = retry
        ? await store.retryApply("volcVoice")
        : await store.applyGroup("volcVoice", changes);
      if (updated.operation.persisted) setDraft({});
      setResult(
        updated.operation.applied
          ? "语音输入配好了。下次聊天，点一下输入框里的麦克风就能试试。"
          : (updated.operation.message ?? "配置尚未应用"),
      );
    } catch (error) {
      setResult(String(error));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="grid gap-3">
      <Select value={mode} onValueChange={(value) => setMode(value as keyof typeof fields)} disabled={busy}>
        <SelectTrigger aria-label="语音凭据类型">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="key">语音 APP Key</SelectItem>
          <SelectItem value="legacy">APP ID + Access Token</SelectItem>
        </SelectContent>
      </Select>
      {mode === "legacy" && stored("VOLC_SPEECH_API_KEY") && (
        <p className="text-sm text-muted">
          语音识别会优先使用已保存的 APP Key。需要切换到旧版凭据时，可在模型与凭据设置中移除 APP Key。
        </p>
      )}
      {fields[mode].map(([name, label]) => (
        <label className="grid gap-1 text-sm" key={name}>
          {label}
          <Input
            type="password"
            autoComplete="off"
            value={draft[name] ?? ""}
            disabled={busy}
            placeholder={stored(name) ? "已配置，留空保留" : "输入凭据"}
            onChange={(event) => setDraft((old) => ({ ...old, [name]: event.target.value }))}
          />
        </label>
      ))}
      <div className="flex gap-2">
        <Button
          variant="outline"
          disabled={
            busy || !changes.length || fields[mode].some(([name]) => !stored(name) && !draft[name]?.trim())
          }
          onClick={() => void save()}
        >
          保存语音配置
        </Button>
        {group?.operation.persisted && !group.operation.applied && !busy && (
          <Button variant="outline" onClick={() => void save(true)}>
            重试应用
          </Button>
        )}
      </div>
      {result && (
        <p role="status" className="text-sm">
          {result}
        </p>
      )}
    </div>
  );
}
