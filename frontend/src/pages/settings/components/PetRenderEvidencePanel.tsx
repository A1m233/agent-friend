import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { SettingsGroup } from "./SettingsGroup";

interface Status { supported: boolean; writerStopped: boolean; dropped: number; writeFailed: number }
export function PetRenderEvidencePanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void invoke<Status>("get_pet_render_evidence_status").then((value) => {
      if (!cancelled) setStatus(value);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  const mark = async () => {
    setSaving(true); setSaved(false);
    try {
      const result = await invoke<{ persisted: boolean }>("mark_pet_render_incident");
      if (!result.persisted) throw new Error("record-not-persisted");
      setSaved(true);
      toast.success("异常记录已保存在本机", { description: "现在可以生成诊断包，也可以退出后再导出。" });
    } catch {
      toast.warning("暂时无法确认记录已保存", {
        description: "可以继续生成诊断包，已留存的历史记录仍会包含在内。",
      });
    } finally { setSaving(false); }
  };
  return (
    <SettingsGroup title="桌宠显示异常记录">
      <p className="text-xs text-muted">
        平时会在本机保留有限的运行记录，不包含截图或聊天内容。遇到闪烁、卡住时，可以保留最近记录，再生成诊断包；重启后仍可导出尚未过期的历史。
      </p>
      {status?.writerStopped || (status?.writeFailed ?? 0) > 0 ? (
        <p className="text-xs text-warning">部分运行记录可能未能保存，诊断包会说明缺失情况。</p>
      ) : null}
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" loading={saving} onClick={() => void mark()}>
          保留最近异常记录
        </Button>
        {saved ? <span className="text-xs text-muted">已保存；运行期间还会补充约 10 秒后续记录。</span> : null}
      </div>
    </SettingsGroup>
  );
}
