import { Checkbox } from "@/components/ui";

export function WeekdaySelection({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <fieldset className="flex flex-wrap gap-3">
    <legend className="mb-2 text-sm">重复日期</legend>
    {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((label, index) => (
      <label key={label} className="flex items-center gap-1 text-sm">
        <Checkbox checked={value.split(",").includes(String(index + 1))} onCheckedChange={(checked) => {
          const selected = value.split(",").filter(Boolean).filter((day) => day !== String(index + 1));
          if (checked === true) selected.push(String(index + 1));
          onChange(selected.sort().join(","));
        }} />{label}
      </label>
    ))}
  </fieldset>;
}
