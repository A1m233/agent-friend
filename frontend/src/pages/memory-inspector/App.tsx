import { useState } from "react";
import { MemoryList } from "./MemoryList";
import { RecallTracePanel } from "./RecallTrace";
import { useLocalStorage } from "./useLocalStorage";
import type { Layer } from "./api";

export function MemoryInspectorApp() {
  const [selectedPersonaId, setSelectedPersonaId] = useLocalStorage<string | null>(
    "mi:personaId",
    null,
  );
  const [highlight, setHighlight] = useState<{ layer: Layer; source_ref: string } | null>(null);

  return (
    <div className="h-screen bg-bg text-fg overflow-hidden">
      <div className="grid grid-cols-2 divide-x divide-border h-full">
        <div className="h-full overflow-hidden">
          <MemoryList
            personaId={selectedPersonaId}
            onPersonaChange={setSelectedPersonaId}
            highlight={highlight}
          />
        </div>
        <div className="h-full overflow-hidden">
          <RecallTracePanel personaId={selectedPersonaId} onHitClick={setHighlight} />
        </div>
      </div>
    </div>
  );
}
