import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import type { AvatarModelCatalogEntry } from "@/avatar-models";

interface Props {
  value: string;
  entries: readonly AvatarModelCatalogEntry[];
  onChange: (id: string) => void;
}

const SHOW_DEV_AVATAR_MODEL_SELECTOR = false;

export function DevAvatarModelSelector({ value, entries, onChange }: Props) {
  // Keep the dev selector wired for runtime smoke, but do not expose it in
  // the pet window until the formal avatar selection UX owns persistence.
  if (!import.meta.env.DEV || !SHOW_DEV_AVATAR_MODEL_SELECTOR) return null;

  return (
    <div
      data-hit
      className="fixed left-2 top-12 z-50 w-72 rounded-md border border-border bg-surface/95 p-2 shadow-sm"
    >
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger data-hit className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {entries.map((entry) => (
            <SelectItem key={entry.id} value={entry.id}>
              {entry.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
