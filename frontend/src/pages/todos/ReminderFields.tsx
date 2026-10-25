import { useState } from "react";
import { Button, Collapsible, CollapsibleContent, CollapsibleTrigger, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import { DateSelection, DateTimeSelection, TimeSelection } from "@/components/scheduling/ScheduleTimeField";
import { WeekdaySelection } from "@/components/scheduling/WeekdaySelection";
import { FrequencySelection } from "@/components/scheduling/FrequencySelection";
import { reminderCadence, withReminderCadence, type ReminderForm } from "./schedule";
import { ReminderPreview } from "./ReminderPreview";

export function ReminderFields({ form, onChange }: { form: ReminderForm; onChange: (form: ReminderForm) => void }) {
  const [cadence, setCadence] = useState(() => reminderCadence(form));
  const [more, setMore] = useState(() => !!(form.until || form.count) || reminderCadence(form) === "custom");
  const set = (key: keyof ReminderForm, value: string) => onChange({ ...form, [key]: value });
  return <div className="grid gap-4">
    <FrequencySelection label="提醒频率" value={cadence} custom onChange={(value) => {
      setCadence(value);
      if (value === "custom") setMore(true);
      onChange(withReminderCadence(form, value));
    }} />
    {form.kind === "once" ? <div className="grid gap-2 text-sm">
      <span>提醒时间</span>
      <DateTimeSelection value={form.onceAt} onChange={(value) => set("onceAt", value)} label="提醒时间" placeholder="选择提醒日期和时间" />
    </div> : <>
      <div className="grid gap-2 text-sm"><span>提醒时间</span><TimeSelection label="提醒" value={form.time} onChange={(value) => set("time", value)} /></div>
      {form.frequency === "weekly" && cadence !== "weekdays" && <WeekdaySelection value={form.weekdays} onChange={(value) => set("weekdays", value)} />}
      <Collapsible open={more} onOpenChange={setMore}>
        <CollapsibleTrigger asChild><Button type="button" variant="outline">更多设置</Button></CollapsibleTrigger>
        <CollapsibleContent className="mt-4 grid gap-4">
          {cadence === "custom" && <div className="grid grid-cols-2 gap-4">
            <label className="grid gap-2 text-sm">重复单位
              <Select value={form.frequency} onValueChange={(value) => set("frequency", value)}>
                <SelectTrigger aria-label="重复单位" className="w-full bg-bg"><SelectValue placeholder="选择重复单位" /></SelectTrigger>
                <SelectContent><SelectItem value="daily">天</SelectItem><SelectItem value="weekly">周</SelectItem><SelectItem value="monthly">月</SelectItem></SelectContent>
              </Select>
            </label>
            <label className="grid gap-2 text-sm">重复间隔<Input aria-label="重复间隔" type="number" min={1} step={1} placeholder="例如：2" value={form.interval} onChange={(event) => set("interval", event.target.value)} /></label>
          </div>}
          {form.frequency === "monthly" && <label className="grid gap-2 text-sm">每月日期
            <Input aria-label="每月日期" placeholder="例如：1,15（每月 1 日和 15 日）" value={form.monthDays} onChange={(event) => set("monthDays", event.target.value)} />
          </label>}
          <div className="grid gap-2 text-sm"><span>开始日期</span><DateSelection label="开始日期" placeholder="选择开始日期" value={form.startsOn} onChange={(value) => set("startsOn", value)} /></div>
          <div className="grid gap-2 text-sm"><span>结束时间（可选）</span><DateTimeSelection label="结束时间" placeholder="不限定结束时间" clearable value={form.until} onChange={(value) => set("until", value)} /></div>
          <label className="grid gap-2 text-sm">最多提醒次数（可选）<Input aria-label="最多提醒次数" type="number" min={1} step={1} placeholder="不填则不限定次数" value={form.count} onChange={(event) => set("count", event.target.value)} /></label>
        </CollapsibleContent>
      </Collapsible>
    </>}
    <ReminderPreview form={form} />
    {form.timezone !== Intl.DateTimeFormat().resolvedOptions().timeZone && <p className="text-xs text-muted">沿用此提醒原来的时区：{form.timezone}</p>}
  </div>;
}
