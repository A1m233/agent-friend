import { useState } from "react";
import { Save, Trash2 } from "lucide-react";
import { Badge, Button, Input } from "@/components/ui";

interface RuntimeSecretFieldProps {
  label: string;
  statusText: string;
  configured: boolean;
  saving: boolean;
  canDelete: boolean;
  helperText?: string | null;
  error?: string | null;
  onSave: (value: string) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}

export function RuntimeSecretField({
  label,
  statusText,
  configured,
  saving,
  canDelete,
  helperText,
  error,
  onSave,
  onDelete,
}: RuntimeSecretFieldProps) {
  const [draft, setDraft] = useState("");

  async function handleSave() {
    const value = draft.trim();
    if (!value) return;
    await onSave(value);
    setDraft("");
  }

  async function handleDelete() {
    if (!canDelete) return;
    if (!window.confirm(`删除 ${label}？`)) return;
    await onDelete();
  }

  return (
    <div className="grid gap-2 border-t border-border py-3 first:border-t-0 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{label}</div>
          <div className="truncate text-xs text-muted">{statusText}</div>
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
            aria-label={`${label} 新值`}
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

      {error ? <div className="text-xs text-danger">{error}</div> : null}
      {helperText ? <div className="text-xs text-muted">{helperText}</div> : null}
    </div>
  );
}
