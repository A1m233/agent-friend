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
  warnings?: DiagnosticCollectionFailure[];
}

export interface GenerateDiagnosticBundleInput {
  description?: string;
  sessionIds: string[];
  sessionTurnLimit?: number | null;
  includeAttachmentOriginals?: boolean;
  includeRenderEvidence?: boolean;
  includeCrashDumps?: boolean;
  allRetainedLogs?: boolean;
  verifyInstallFiles?: boolean;
}

export interface DiagnosticJob {
  id: string;
  ordinal: number;
  version: number;
  status: "running" | "cancelling" | "publishing" | "complete" | "partial" | "cancelled" | "failed";
  stage: string;
  processedBytes: number;
  elapsedMs: number;
  result: GenerateDiagnosticBundleResult | null;
  error: string | null;
  selection?: {
    logs: "recent" | "all-retained";
    sessionCount: number;
    sessionTurnLimit: number | null;
    includeAttachmentOriginals: boolean;
    includeCrashDumps: boolean;
    verifyInstallFiles: boolean;
  };
}

export function diagnosticJobActive(job: DiagnosticJob | null): boolean {
  return job != null && ["running", "cancelling", "publishing"].includes(job.status);
}

export function getDiagnosticJob(): Promise<DiagnosticJob | null> {
  return invoke("get_diagnostic_bundle_job");
}

export function getDiagnosticLogEstimate(): Promise<{ retainedBytes: number; fileCount: number }> {
  return invoke("get_diagnostic_log_estimate");
}

export function cancelDiagnosticJob(jobId: string): Promise<DiagnosticJob> {
  return invoke("cancel_diagnostic_bundle", { jobId });
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

export function startDiagnosticBundle(
  input: GenerateDiagnosticBundleInput,
): Promise<DiagnosticJob> {
  const resolved = Intl.DateTimeFormat().resolvedOptions();
  return invoke("start_diagnostic_bundle", {
    request: {
      description: input.description || null,
      sessionIds: input.sessionIds,
      sessionTurnLimit: input.sessionTurnLimit === undefined ? 10 : input.sessionTurnLimit,
      locale: navigator.language || null,
      timezone: resolved.timeZone || null,
      includeAttachmentOriginals: input.includeAttachmentOriginals ?? false,
      includeRenderEvidence: input.includeRenderEvidence ?? false,
      includeCrashDumps: input.includeCrashDumps ?? false,
      allRetainedLogs: input.allRetainedLogs ?? false,
      verifyInstallFiles: input.verifyInstallFiles ?? false,
    },
  });
}

// Existing callers can await completion; ownership stays in Rust when a page unmounts.
export class DiagnosticExportCancelled extends Error {
  constructor(message = "诊断导出已取消") {
    super(message);
    this.name = "DiagnosticExportCancelled";
  }
}

export async function generateDiagnosticBundle(input: GenerateDiagnosticBundleInput): Promise<GenerateDiagnosticBundleResult> {
  let job = await startDiagnosticBundle(input);
  const id = job.id;
  while (diagnosticJobActive(job)) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const next = await getDiagnosticJob();
    if (!next || next.id !== id) throw new Error("诊断任务已变化，请查看最新状态。");
    job = next;
  }
  if (job.result) return job.result;
  if (job.status === "cancelled") throw new DiagnosticExportCancelled(job.error || undefined);
  throw new Error(job.error || "诊断导出失败");
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
