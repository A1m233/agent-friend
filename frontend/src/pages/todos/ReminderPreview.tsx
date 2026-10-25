import { useEffect, useState } from "react";
import { previewSchedule } from "@/components/scheduling/preview";
import { formatTimestamp } from "@/components/scheduling/presentation";
import { friendlyErrorMessage } from "@/services/http";
import { buildReminderSchedule, type ReminderForm } from "./schedule";

export function ReminderPreview({ form }: { form: ReminderForm }) {
  let key = "";
  let invalid = "";
  try { key = JSON.stringify(buildReminderSchedule(form)); }
  catch (error) { invalid = error instanceof Error ? error.message : "请完善提醒设置"; }
  const [result, setResult] = useState({ key: "", text: "" });
  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void previewSchedule(JSON.parse(key) as Record<string, unknown>).then((next) => {
        if (!cancelled) setResult({ key, text: next ? formatTimestamp(next) : "没有未来提醒时间" });
      }).catch((error) => {
        if (!cancelled) setResult({ key, text: friendlyErrorMessage(error) });
      });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [key]);
  return <p className="text-xs text-muted" aria-live="polite">预计下次提醒：{invalid || (result.key === key ? result.text : "计算中…")}</p>;
}
