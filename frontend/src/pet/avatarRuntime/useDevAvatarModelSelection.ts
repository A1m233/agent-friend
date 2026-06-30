import { useMemo, useState } from "react";
import {
  BUILTIN_ARK_SPINE_MODELS,
  DEFAULT_AVATAR_MODEL_ID,
  HIYORI_LIVE2D_MODEL,
  type AvatarModelCatalogEntry,
} from "@/avatar-models";

export function useDevAvatarModelSelection(): {
  selectedModelId: string;
  selectedEntry: AvatarModelCatalogEntry;
  selectableEntries: readonly AvatarModelCatalogEntry[];
  setSelectedModelId: (id: string) => void;
} {
  const selectableEntries = useMemo<readonly AvatarModelCatalogEntry[]>(
    () =>
      import.meta.env.DEV
        ? [HIYORI_LIVE2D_MODEL, ...BUILTIN_ARK_SPINE_MODELS]
        : [HIYORI_LIVE2D_MODEL],
    [],
  );
  const [selectedModelId, setSelectedModelId] = useState(DEFAULT_AVATAR_MODEL_ID);
  const selectedEntry =
    selectableEntries.find((entry) => entry.id === selectedModelId) ?? HIYORI_LIVE2D_MODEL;

  return {
    selectedModelId: selectedEntry.id,
    selectedEntry,
    selectableEntries,
    setSelectedModelId,
  };
}
