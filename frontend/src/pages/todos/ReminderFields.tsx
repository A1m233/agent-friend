import { Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import type { ReminderForm } from "./schedule";

export function ReminderFields({ form, onChange }: { form: ReminderForm; onChange: (form: ReminderForm) => void }) {
  const set = (key: keyof ReminderForm, value: string) => onChange({ ...form, [key]: value });
  const field = (key: keyof ReminderForm, label: string, type = "text", placeholder?: string) => (
    <label className="flex flex-col gap-1 text-sm">{label}
      <Input aria-label={label} type={type} step={type === "time" || type === "datetime-local" ? 1 : undefined} value={form[key]} placeholder={placeholder}
        onChange={(e) => set(key, e.target.value)} />
    </label>
  );
  return <div className="grid gap-3">
    <label className="flex flex-col gap-1 text-sm">提醒方式
      <Select value={form.kind} onValueChange={(value) => set("kind", value)}>
        <SelectTrigger aria-label="提醒方式"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="once">一次性</SelectItem><SelectItem value="recurring">重复提醒</SelectItem></SelectContent>
      </Select>
    </label>
    {form.kind === "once" ? field("onceAt", "提醒时间", "datetime-local") : <>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">重复单位
          <Select value={form.frequency} onValueChange={(value) => set("frequency", value)}>
            <SelectTrigger aria-label="重复单位"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="daily">天</SelectItem><SelectItem value="weekly">周</SelectItem><SelectItem value="monthly">月</SelectItem></SelectContent>
          </Select>
        </label>
        {field("interval", "每隔几个单位", "number")}
      </div>
      {field("startsOn", "开始日期", "date")}
      {field("time", "每天提醒时刻", "time")}
      {form.frequency === "weekly" && field("weekdays", "星期（1 为周一，7 为周日）", "text", "1,3,5")}
      {form.frequency === "monthly" && field("monthDays", "每月日期", "text", "1,15")}
      <div className="grid gap-3 sm:grid-cols-2">
        {field("until", "结束时间（可选）", "datetime-local")}
        {field("count", "最多提醒次数（可选）", "number")}
      </div>
    </>}
    {field("timezone", "时区", "text", "Asia/Shanghai")}
    <label className="flex flex-col gap-1 text-sm">夏令时重复时刻
      <Select value={form.fold} onValueChange={(value) => set("fold", value)}>
        <SelectTrigger aria-label="夏令时重复时刻"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="0">第一次出现</SelectItem><SelectItem value="1">第二次出现</SelectItem></SelectContent>
      </Select>
    </label>
  </div>;
}
