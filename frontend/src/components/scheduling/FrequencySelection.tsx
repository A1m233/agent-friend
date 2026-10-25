import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";

export function FrequencySelection({ label, value, onChange, custom = false, existing }: {
  label: string; value: string; onChange: (value: string) => void; custom?: boolean; existing?: string;
}) {
  return <label className="flex flex-col gap-2 text-sm">{label}
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className="w-full bg-bg"><SelectValue placeholder="选择频率" /></SelectTrigger>
      <SelectContent>
        {existing && <SelectItem value="existing" disabled>{existing}</SelectItem>}
        <SelectItem value="once">仅一次</SelectItem>
        <SelectItem value="daily">每天</SelectItem>
        <SelectItem value="weekdays">工作日</SelectItem>
        <SelectItem value="weekly">每周</SelectItem>
        {custom && <SelectItem value="custom">自定义</SelectItem>}
      </SelectContent>
    </Select>
  </label>;
}
