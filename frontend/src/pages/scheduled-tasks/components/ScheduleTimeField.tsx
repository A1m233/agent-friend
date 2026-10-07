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

/** Page-level composition of shadcn primitives, not a separate vendor DateTimePicker. */
export function TimeSelection({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [hour, minute] = value.split(":");
  return (
    <div className="flex items-center gap-2">
      <Select value={hour} onValueChange={(next) => onChange(`${next}:${minute}`)}>
        <SelectTrigger aria-label="执行小时" className="w-full bg-bg">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {hours.map((item) => <SelectItem key={item} value={item}>{item} 时</SelectItem>)}
        </SelectContent>
      </Select>
      <span aria-hidden="true">:</span>
      <Select value={minute} onValueChange={(next) => onChange(`${hour}:${next}`)}>
        <SelectTrigger aria-label="执行分钟" className="w-full bg-bg">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {minutes.map((item) => <SelectItem key={item} value={item}>{item} 分</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}

export function DateTimeSelection({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
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
        <Button variant="outline" aria-label="执行日期和时间" className="w-full justify-between font-normal">
          {value ? value.replace("T", " ") : <span className="text-muted">选择日期和时间</span>}
          <CalendarDays className="size-4 text-muted" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto bg-bg p-0" aria-label="选择执行日期和时间">
        <Calendar
          mode="single"
          required
          locale={zhCN}
          weekStartsOn={1}
          selected={selected}
          defaultMonth={selected}
          autoFocus
          onSelect={(next) => {
            if (next) onChange(`${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${time}`);
          }}
        />
        <div className="flex flex-col gap-3 border-t border-border p-3">
          <TimeSelection value={time} onChange={(next) => {
            setDraftTime(next);
            if (datePart) onChange(`${datePart}T${next}`);
          }} />
          <Button onClick={() => setOpen(false)} disabled={!datePart}>完成</Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
