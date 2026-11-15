import { ReasoningSelection } from "@/components/model-config/ReasoningSelection";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import {
  defaultModelSelection,
  modelProvidersFromCatalog,
  providerModelsFromCatalog,
  useModelCatalogStore,
} from "@/lib/model-config";
import type { ModelProvider, ModelSelection } from "@/lib/settings";
import { SettingsRow } from "./SettingsRow";

export function ModelSelectionRows({
  selection,
  purpose,
  modelLabel,
  onChange,
  disabled,
  allowedModels,
}: {
  selection: ModelSelection;
  purpose: "chat" | "memory";
  modelLabel: string;
  onChange: (next: ModelSelection) => void;
  disabled?: boolean;
  allowedModels?: readonly string[];
}) {
  const catalog = useModelCatalogStore((state) => state.catalog);
  const isAllowed = (id: string) => !allowedModels || allowedModels.includes(id);
  const providers = modelProvidersFromCatalog(catalog).filter(
    (provider) => !allowedModels || provider.models.some((model) => isAllowed(model.id)),
  );
  const models = providerModelsFromCatalog(catalog, selection.provider).filter((model) => isAllowed(model.id));
  const reasoning = models.find((model) => model.id === selection.model)?.reasoning;

  return (
    <>
      <SettingsRow label="调用渠道">
        <Select
          value={selection.provider}
          disabled={disabled}
          onValueChange={(value) => {
            const next = defaultModelSelection(value as ModelProvider, purpose);
            if (!next) return;
            if (isAllowed(next.model)) {
              onChange(next);
            } else {
              const first = providerModelsFromCatalog(catalog, next.provider).find((m) => isAllowed(m.id));
              if (first) onChange({ provider: next.provider, model: first.id });
            }
          }}
        >
          <SelectTrigger className="min-w-48" aria-label="调用渠道">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {providers.map((provider) => (
              <SelectItem key={provider.id} value={provider.id}>
                {provider.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsRow>
      <SettingsRow label={modelLabel}>
        <Select
          value={selection.model}
          disabled={disabled}
          onValueChange={(value) =>
            void onChange({ provider: selection.provider, model: value })
          }
        >
          <SelectTrigger className="min-w-64" aria-label={modelLabel}>
            <SelectValue>{models.find((model) => model.id === selection.model)?.label ?? selection.model}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {models.map((model) => (
              <SelectItem key={model.id} value={model.id}>
                {model.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsRow>
      {reasoning && (
        <SettingsRow label="推理强度">
          <ReasoningSelection
            key={selection.model}
            definition={reasoning}
            value={selection.reasoningPreset ?? reasoning.defaultPreset}
            disabled={disabled}
            onChange={(reasoningPreset) => onChange({ ...selection, reasoningPreset })}
          />
        </SettingsRow>
      )}
    </>
  );
}
