import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { useSetting, type NotificationSound } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

export function NotificationSoundSettings() {
  const [source, , acceptConfirmed] = useSetting("notificationSound");
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const previewId = useRef<string | null>(null);
  const mounted = useRef(false);
  const assetId = source.kind === "custom" ? source.assetId : "default";

  const stopPreview = () => {
    const id = previewId.current;
    previewId.current = null;
    setPreviewing(false);
    if (id) return invoke("stop_notification_sound_preview", { previewId: id });
    return Promise.resolve();
  };

  useEffect(() => {
    mounted.current = true;
    setPreviewing(false);
    setAssetError(null);
    let current = true;
    void invoke<string | null>("notification_sound_status").then((value) => {
      if (current) setAssetError(value);
    }).catch(() => { if (current) setAssetError("无法检查提示音状态，请重试"); });
    return () => {
      current = false;
      mounted.current = false;
      const id = previewId.current;
      previewId.current = null;
      if (id) void invoke("stop_notification_sound_preview", { previewId: id }).catch(() => undefined);
    };
  }, [assetId]);

  useEffect(() => {
    if (!previewing) return;
    let pending = false;
    const timer = window.setInterval(() => {
      const id = previewId.current;
      if (!id || pending) return;
      pending = true;
      void invoke<boolean>("notification_sound_preview_active", { previewId: id }).then((active) => {
        if (!active && previewId.current === id) { previewId.current = null; setPreviewing(false); }
      }).catch(() => {
        if (previewId.current === id) { previewId.current = null; setPreviewing(false); }
      }).finally(() => { pending = false; });
    }, 500);
    return () => window.clearInterval(timer);
  }, [previewing]);

  const change = async (command: "import_notification_sound" | "reset_notification_sound") => {
    setBusy(true); setError(null);
    try {
      await stopPreview();
      const next = await invoke<NotificationSound | null>(command);
      if (next) acceptConfirmed(next);
    } catch (reason) {
      if (mounted.current) setError(String(reason));
    } finally { if (mounted.current) setBusy(false); }
  };

  const preview = async () => {
    setError(null);
    if (previewId.current) {
      try { await stopPreview(); } catch (reason) { setError(String(reason)); }
      return;
    }
    const id = crypto.randomUUID();
    previewId.current = id;
    setBusy(true);
    try {
      await invoke("preview_notification_sound", { previewId: id });
      if (!mounted.current || previewId.current !== id) {
        await invoke("stop_notification_sound_preview", { previewId: id });
      } else { setPreviewing(true); }
    } catch (reason) {
      if (mounted.current && previewId.current === id) setError(String(reason));
      if (previewId.current === id) previewId.current = null;
    } finally { if (mounted.current) setBusy(false); }
  };

  return <div className="flex min-w-0 flex-col gap-2">
    <SettingsRow label="提示音">
      <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
        <span className="max-w-64 truncate text-sm text-muted" title={source.kind === "custom" ? source.name : "默认"}>
          {source.kind === "custom" ? source.name : "默认"}
        </span>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void change("import_notification_sound")}>
          {source.kind === "custom" ? "替换音频" : "选择音频"}
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void preview()}>{previewing ? "停止试听" : "试听"}</Button>
        {source.kind === "custom" && <Button variant="ghost" size="sm" disabled={busy} onClick={() => void change("reset_notification_sound")}>恢复默认</Button>}
      </div>
    </SettingsRow>
    <p className="text-xs leading-relaxed text-muted">支持 MP3、WAV，最大 10 MiB、最长 30 秒。导入后无需保留原文件；关闭提示音后仍可试听。</p>
    {(error || assetError) && <p className="text-xs text-danger" role="alert">{error || `${assetError}；通知将使用默认提示音。`}</p>}
  </div>;
}
