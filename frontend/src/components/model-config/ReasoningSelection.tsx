import { useState } from "react";
import { Button, Input, Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui";
import type { ModelReasoningDefinition } from "@/lib/model-config";

export function CustomReasoningInput({ disabled, onChange }: { disabled?: boolean; onChange: (value: string) => void }) {
  const [value, setValue] = useState("");
  return <div className="space-y-2 p-2" onKeyDown={(event) => event.stopPropagation()}>
    <Input aria-label="自定义推理强度" placeholder="填写服务接受的值" maxLength={128} value={value} disabled={disabled} onChange={(event) => setValue(event.target.value)} />
    <Button size="sm" variant="outline" disabled={disabled || !value.trim() || Array.from(value).some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)} onClick={() => onChange(`effort:${value.trim()}`)}>使用此强度</Button>
    <p className="text-xs text-muted">常见值仅供参考，是否支持由服务决定。</p>
  </div>;
}

export function ReasoningSelection({ definition, value, disabled, onChange }: { definition: ModelReasoningDefinition; value: string; disabled?: boolean; onChange: (value: string) => void }) {
  const known = definition.presets.some((item) => item.id === value);
  return <div className="min-w-64 space-y-2">
    <Select value={value} disabled={disabled || definition.control === "fixed" || definition.control === "unsupported"} onValueChange={onChange}>
      <SelectTrigger className="ml-auto" aria-label="推理强度"><SelectValue /></SelectTrigger>
      <SelectContent>
        {definition.presets.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}
        {!known && <SelectItem value={value}>{value.startsWith("effort:") ? value.slice(7) : value}</SelectItem>}
      </SelectContent>
    </Select>
    {definition.allowCustom && <CustomReasoningInput disabled={disabled} onChange={onChange} />}
  </div>;
}
