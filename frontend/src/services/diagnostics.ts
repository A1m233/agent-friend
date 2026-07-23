import { invoke } from "@tauri-apps/api/core";

export interface DiagnosticCollectionFailure {
  collection: string;
  item?: string | null;
  message: string;
}

export interface GenerateDiagnosticBundleResult {
  path: string;
  outcome: "complete" | "partial";
  includedSessionIds: string[];
  failures: DiagnosticCollectionFailure[];
}

export interface GenerateDiagnosticBundleInput {
  description?: string;
  sessionIds: string[];
  includeAttachmentOriginals?: boolean;
}

export function generateDiagnosticBundle(
  input: GenerateDiagnosticBundleInput,
): Promise<GenerateDiagnosticBundleResult> {
  const resolved = Intl.DateTimeFormat().resolvedOptions();
  return invoke("generate_diagnostic_bundle", {
    request: {
      description: input.description || null,
      sessionIds: input.sessionIds,
      locale: navigator.language || null,
      timezone: resolved.timeZone || null,
      includeAttachmentOriginals: input.includeAttachmentOriginals ?? false,
    },
  });
}
