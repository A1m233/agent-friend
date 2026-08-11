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
  includeRenderEvidence?: boolean;
}

export interface RenderEvidencePendingSummary {
  captureId: string;
  capturedAt: string;
  trigger: string;
  complete: boolean;
  webview2Available: boolean;
  desktopAvailable: boolean;
  errorCount: number;
  automaticIncident: boolean;
}

export interface RenderEvidenceStatus {
  supported: boolean;
  armed: boolean;
  waitingForVisibleAfterHidden: boolean;
  captureInProgress: boolean;
  pending: RenderEvidencePendingSummary | null;
  storageError: string | null;
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
      includeRenderEvidence: input.includeRenderEvidence ?? false,
    },
  });
}

export function getRenderEvidenceStatus(): Promise<RenderEvidenceStatus> {
  return invoke("get_webview_render_evidence_status");
}

export function setRenderEvidenceArmed(armed: boolean): Promise<RenderEvidenceStatus> {
  return invoke("set_webview_render_evidence_armed", { armed });
}

export function captureRenderEvidenceNow(): Promise<RenderEvidenceStatus> {
  return invoke("capture_webview_render_evidence_now");
}

export function clearRenderEvidence(): Promise<RenderEvidenceStatus> {
  return invoke("clear_webview_render_evidence");
}

export function recordPetWebviewVisibility(
  visibilityState: "hidden" | "visible",
): Promise<RenderEvidenceStatus> {
  return invoke("record_pet_webview_visibility", { visibilityState });
}
