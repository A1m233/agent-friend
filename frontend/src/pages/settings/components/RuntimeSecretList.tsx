import type {
  RuntimeConfigItemStatus,
  RuntimeSecretName,
} from "@/lib/runtime-config";
import { RuntimeSecretField } from "./RuntimeSecretField";

export interface RuntimeSecretListItem {
  name: RuntimeSecretName;
  label: string;
  status?: RuntimeConfigItemStatus;
  statusText: string;
  helperText?: string | null;
}

interface RuntimeSecretListProps {
  items: RuntimeSecretListItem[];
  savingName: RuntimeSecretName | null;
  onSave: (name: RuntimeSecretName, value: string) => Promise<unknown>;
  onDelete: (name: RuntimeSecretName) => Promise<unknown>;
}

export function RuntimeSecretList({
  items,
  savingName,
  onSave,
  onDelete,
}: RuntimeSecretListProps) {
  return (
    <div className="grid gap-0">
      {items.map((item) => (
        <RuntimeSecretField
          key={item.name}
          label={item.label}
          statusText={item.statusText}
          configured={item.status?.configured ?? false}
          saving={savingName === item.name}
          canDelete={item.status?.source === "keyring"}
          helperText={item.helperText}
          error={item.status?.error}
          onSave={(value) => onSave(item.name, value)}
          onDelete={() => onDelete(item.name)}
        />
      ))}
    </div>
  );
}
