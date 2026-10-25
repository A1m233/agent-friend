import { useState } from "react";
import { CalendarDays } from "lucide-react";
import { zhCN } from "react-day-picker/locale";
import {
  Button,
  Calendar,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";

const pad = (value: number) => String(value).padStart(2, "0");
const hours = Array.from({ length: 24 }, (_, index) => pad(index));
const minutes = Array.from({ length: 60 }, (_, index) => pad(index));

/** Shared scheduling composition of existing shadcn primitives. */
export function TimeSelection({ value, onChange, label = "执行" }: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}) {
  const [hour, minute] = value.split(":");
  const suffix = value.split(":").slice(2).join(":");
  const update = (h: string, m: string) => onChange(`${h}:${m}${suffix ? `:${suffix}` : ""}`);
  return (
    <div className="flex items-center gap-2">
      <Select value={hour || ""} onValueChange={(next) => update(next, minute || "00")}>
        <SelectTrigger aria-label={`${label}小时`} className="w-full bg-bg">
          <SelectValue placeholder="选择小时" />
        </SelectTrigger>
        <SelectContent>
          {hours.map((item) => <SelectItem key={item} value={item}>{item} 时</SelectItem>)}
        </SelectContent>
      </Select>
      <span aria-hidden="true">:</span>
      <Select value={minute || ""} onValueChange={(next) => update(hour || "09", next)}>
        <SelectTrigger aria-label={`${label}分钟`} className="w-full bg-bg">
          <SelectValue placeholder="选择分钟" />
        </SelectTrigger>
        <SelectContent>
          {minutes.map((item) => <SelectItem key={item} value={item}>{item} 分</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}

export function DateTimeSelection({ value, onChange, label = "执行日期和时间", placeholder = "选择日期和时间", dateOnly = false, clearable = false }: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
  dateOnly?: boolean;
  clearable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draftTime, setDraftTime] = useState("09:00");
  const [datePart, timePart] = value.split("T");
  const time = timePart || draftTime;
  const [year, month, day] = datePart.split("-").map(Number);
  // Calendar dates are wall-clock values; never serialize through UTC/toISOString.
  const selected = datePart ? new Date(year, month - 1, day, 12) : undefined;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" aria-label={label} className="w-full justify-between font-normal">
          {value ? value.replace("T", " ") : <span className="text-muted">{placeholder}</span>}
          <CalendarDays className="size-4 text-muted" />
        </Button>
      </PopoverTrigger>
      {open && <PopoverContent align="start" className="w-auto bg-bg p-0" aria-label={`选择${label}`}>
        <Calendar
          mode="single"
          required
          locale={zhCN}
          weekStartsOn={1}
          selected={selected}
          defaultMonth={selected}
          autoFocus
          onSelect={(next) => {
            if (next) {
              const date = `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`;
              onChange(dateOnly ? date : `${date}T${time}`);
              if (dateOnly) setOpen(false);
            }
          }}
        />
        <div className="flex flex-col gap-3 border-t border-border p-3">
          {!dateOnly && <TimeSelection label={label === "执行日期和时间" ? "执行" : label} value={time} onChange={(next) => {
            setDraftTime(next);
            if (datePart) onChange(`${datePart}T${next}`);
          }} />}
          {clearable && <Button type="button" variant="ghost" onClick={() => { onChange(""); setOpen(false); }}>清除</Button>}
          <Button type="button" onClick={() => setOpen(false)} disabled={!datePart}>完成</Button>
        </div>
      </PopoverContent>}
    </Popover>
  );
}

export function DateSelection(props: Omit<Parameters<typeof DateTimeSelection>[0], "dateOnly">) {
  return <DateTimeSelection {...props} dateOnly />;
}
