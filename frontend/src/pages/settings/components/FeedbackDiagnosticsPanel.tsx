import { DiagnosticExportCancelled, diagnosticJobActive, getDiagnosticLogEstimate } from "@/services/diagnostics";
import { startDiagnosticExportSync, useDiagnosticExportStore } from "@/stores/diagnosticExport";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { PetRenderEvidencePanel } from "./PetRenderEvidencePanel";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  Copy,
  FileArchive,
  FolderOpen,
  MonitorCog,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import dayjs from "dayjs";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { toast } from "sonner";

import { Button, Checkbox, Input, ScrollArea, Textarea } from "@/components/ui";
import {
  captureRenderEvidenceNow,
  clearRenderEvidence,
  generateDiagnosticBundle,
  getRenderEvidenceStatus,
  setRenderEvidenceArmed,
  type GenerateDiagnosticBundleResult,
  type RenderEvidenceStatus,
} from "@/services";
import { useArchivedSessionsStore, useSessionsStore } from "@/stores";
import type { SessionSummary } from "@/types/meta";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";

export interface FeedbackDiagnosticsPrefill {
  requestId: number;
  sessionId?: string;
}

function resultTitle(result: GenerateDiagnosticBundleResult): string {
  if (result.outcome === "partial") return "诊断包已生成，但部分内容获取失败";
  if (result.warnings?.some((warning) => warning.collection === "temporary-files")) return "诊断包已生成，但临时文件清理未完成";
  return result.warnings?.length
    ? "诊断包已生成，部分历史证据不完整"
    : "诊断包已生成";
}

export function FeedbackDiagnosticsPanel({
  prefill,
}: {
  prefill: FeedbackDiagnosticsPrefill | null;
}) {
  const sessions = useSessionsStore((state) => state.list);
  const sessionsLoading = useSessionsStore((state) => state.loading);
  const sessionsLoaded = useSessionsStore((state) => state.loaded);
  const refreshSessions = useSessionsStore((state) => state.refresh);
  const archivedSessions = useArchivedSessionsStore((state) => state.list);
  const archivedLoading = useArchivedSessionsStore((state) => state.loading);
  const archivedLoaded = useArchivedSessionsStore((state) => state.loaded);
  const refreshArchived = useArchivedSessionsStore((state) => state.refresh);
  const [description, setDescription] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const descriptionRevision = useRef(0);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [submitting, setGenerating] = useState(false);
  const job = useDiagnosticExportStore((state) => state.job);
  const jobError = useDiagnosticExportStore((state) => state.error);
  const cancelExport = useDiagnosticExportStore((state) => state.cancel);
  const generating = submitting || diagnosticJobActive(job);
  useEffect(startDiagnosticExportSync, []);
  const [logEstimate, setLogEstimate] = useState<number | null>(null);
  useEffect(() => {
    let stopped = false;
    void getDiagnosticLogEstimate().then((value) => { if (!stopped) setLogEstimate(value.retainedBytes); }).catch(() => {});
    return () => { stopped = true; };
  }, []);
  const [allRetainedLogs, setAllRetainedLogs] = useState(false);
  const [verifyInstallFiles, setVerifyInstallFiles] = useState(false);
  const [allTurns, setAllTurns] = useState(false);
  const [turnLimit, setTurnLimit] = useState("10");
  const validTurnLimit = /^\d+$/.test(turnLimit) && Number(turnLimit) >= 1 && Number(turnLimit) <= 100;
  const [includeAttachmentOriginals, setIncludeAttachmentOriginals] = useState(false);
  const [includeRenderEvidence, setIncludeRenderEvidence] = useState(false);
  const [includeCrashDumps, setIncludeCrashDumps] = useState(false);
  const [renderEvidenceStatus, setRenderEvidenceStatus] =
    useState<RenderEvidenceStatus | null>(null);
  const [renderEvidenceUpdating, setRenderEvidenceUpdating] = useState(false);
  const [localResult, setResult] = useState<GenerateDiagnosticBundleResult | null>(null);
  const result = generating ? null : job?.result ?? localResult;
  const appliedPrefillRequestId = useRef(0);

  useEffect(() => {
    void Promise.all([refreshSessions(), refreshArchived()]);
  }, [refreshArchived, refreshSessions]);

  useEffect(() => {
    let cancelled = false;
    void getRenderEvidenceStatus()
      .then((status) => {
        if (cancelled) return;
        setRenderEvidenceStatus(status);
      })
      .catch(() => {
        if (!cancelled) setRenderEvidenceStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const combinedSessions = useMemo(() => {
    const byId = new Map<string, SessionSummary>();
    for (const session of [...sessions, ...archivedSessions]) {
      byId.set(session.session_id, session);
    }
    return [...byId.values()].sort((a, b) =>
      b.updated_at.localeCompare(a.updated_at),
    );
  }, [archivedSessions, sessions]);

  // Focus refreshes the shared stores. Keep cached rows mounted so the
  // content-sized list and the controls below it do not collapse while fetching.
  const initialLoading = combinedSessions.length === 0 && (
    (sessionsLoading && !sessionsLoaded) || (archivedLoading && !archivedLoaded)
  );

  useEffect(() => {
    if (!prefill || prefill.requestId <= appliedPrefillRequestId.current) return;
    if (
      prefill.sessionId &&
      !combinedSessions.some((session) => session.session_id === prefill.sessionId)
    ) {
      return;
    }
    appliedPrefillRequestId.current = prefill.requestId;
    setSelectedIds(prefill.sessionId ? new Set([prefill.sessionId]) : new Set());
  }, [combinedSessions, prefill]);

  const toggleSession = (sessionId: string, checked: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  };

  const reveal = async (path: string) => {
    try {
      await revealItemInDir(path);
    } catch {
      toast.warning("诊断包已生成，但无法打开所在目录", {
        description: "完整路径仍保留在页面中，可以复制后手动打开。",
      });
    }
  };

  const generate = async () => {
    if (selectedIds.size > 0 && !allTurns && !validTurnLimit) return;
    const submittedRevision = descriptionRevision.current;
    setGenerating(true);
    setResult(null);
    try {
      const next = await generateDiagnosticBundle({
        description: description.trim() || undefined,
        sessionIds: [...selectedIds],
        sessionTurnLimit: allTurns ? null : selectedIds.size === 0 ? 10 : Number(turnLimit),
        includeAttachmentOriginals,
        includeRenderEvidence,
        includeCrashDumps,
        allRetainedLogs,
        verifyInstallFiles,
      });
      setResult(next);
      // A generated bundle contains the submitted description, including partial exports.
      // Preserve any edits made while the export was in flight.
      if (descriptionRevision.current === submittedRevision) setDescription("");
      const partial = next.outcome === "partial";
      const notify = partial ? toast.warning : toast.success;
      notify(resultTitle(next), {
        action: {
          label: "打开所在目录",
          onClick: () => void reveal(next.path),
        },
      });
    } catch (error) {
      if (error instanceof DiagnosticExportCancelled) return;
      const message = error instanceof Error ? error.message : String(error);
      toast.error("无法生成诊断包", { description: message });
    } finally {
      setGenerating(false);
      setIncludeCrashDumps(false);
    }
  };

  const updateRenderEvidenceArmed = async (armed: boolean) => {
    setRenderEvidenceUpdating(true);
    try {
      const status = await setRenderEvidenceArmed(armed);
      setRenderEvidenceStatus(status);
      toast.success(armed ? "已等待下一次息屏恢复" : "已取消一次性渲染取证");
    } catch (error) {
      toast.error("无法更新渲染取证状态", {
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setRenderEvidenceUpdating(false);
    }
  };

  const captureRenderEvidence = async () => {
    setRenderEvidenceUpdating(true);
    try {
      const status = await captureRenderEvidenceNow();
      setRenderEvidenceStatus(status);
      setIncludeRenderEvidence(false);
      if (status.pending?.complete) {
        toast.success("渲染证据已保存在本机");
      } else {
        toast.warning("渲染证据已保存，但部分内容采集失败");
      }
    } catch (error) {
      toast.error("无法采集渲染证据", {
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setRenderEvidenceUpdating(false);
    }
  };

  const clearPendingRenderEvidence = async () => {
    setRenderEvidenceUpdating(true);
    try {
      const status = await clearRenderEvidence();
      setRenderEvidenceStatus(status);
      setIncludeRenderEvidence(false);
      toast.success("已清除本机渲染证据");
    } catch (error) {
      toast.error("无法清除渲染证据", {
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setRenderEvidenceUpdating(false);
    }
  };

  const copyPath = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.path);
      toast.success("路径已复制");
    } catch {
      toast.error("复制失败，请手动选择路径");
    }
  };

  const extraOptions = [
    allRetainedLogs && "全部日志",
    verifyInstallFiles && "安装文件校验",
    selectedIds.size > 0 && includeAttachmentOriginals && "附件原件",
    includeCrashDumps && "原始崩溃转储",
    (renderEvidenceStatus?.pending?.automaticIncident || includeRenderEvidence) && "渲染截图",
  ].filter(Boolean);
  const includesScreenshots = renderEvidenceStatus?.pending?.automaticIncident || includeRenderEvidence;

  return (
    <SettingsPageLayout title="帮助与反馈">
      <SettingsGroup title="选择相关会话" description="可多选；不选会话也能导出基础诊断信息。">
        <ScrollArea className="rounded-md border border-border" viewportClassName="max-h-60">
          <div className="flex flex-col p-2">
            {initialLoading ? (
              <p className="p-3 text-sm text-muted">正在加载会话…</p>
            ) : combinedSessions.length === 0 ? (
              <p className="p-3 text-sm text-muted">暂无可导出的已持久化会话</p>
            ) : (
              combinedSessions.map((session) => {
                const archived = Boolean(session.archived_at);
                return (
                  <label
                    key={session.session_id}
                    className="flex cursor-pointer select-none items-start gap-3 rounded-md p-3 hover:bg-surface-hover"
                  >
                    <Checkbox
                      className="mt-0.5"
                      checked={selectedIds.has(session.session_id)}
                      disabled={generating}
                      onCheckedChange={(checked) =>
                        toggleSession(session.session_id, checked === true)
                      }
                      aria-label={`选择会话 ${session.title || "未命名对话"}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-fg">
                        <span className="min-w-0 flex-1 truncate">
                          {session.title || "未命名对话"}
                        </span>
                        {archived ? (
                          <span className="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs text-muted">
                            已归档
                          </span>
                        ) : null}
                      </span>
                      <span className="mt-1 block text-xs text-muted">
                        最近活跃 {dayjs(session.updated_at).format("YYYY-MM-DD HH:mm")}
                      </span>
                    </span>
                  </label>
                );
              })
            )}
          </div>
        </ScrollArea>
        {selectedIds.size > 0 ? (
          <div className="flex flex-col gap-3">
            <p className="text-xs text-muted">已选择 {selectedIds.size} 个会话</p>
            <label className="flex flex-wrap items-center gap-3">
              <span className="text-sm font-medium text-fg">每个会话最近</span>
              <Input
                type="number"
                min={1}
                max={100}
                step={1}
                value={turnLimit}
                disabled={allTurns || generating}
                onChange={(event) => setTurnLimit(event.target.value)}
                aria-label="采集最近轮数"
                aria-invalid={!allTurns && !validTurnLimit}
                className="w-20"
              />
              <span className="text-sm text-fg">轮</span>
            </label>
            <label className="flex select-none items-center gap-3">
              <Checkbox checked={allTurns} disabled={generating}
                onCheckedChange={(checked) => setAllTurns(checked === true)}
                aria-label="采集全部轮次" />
              <span className="text-sm text-fg">采集全部轮次（可能耗时较长）</span>
            </label>
            {!allTurns && !validTurnLimit ? (
              <p role="alert" className="text-xs text-danger">请输入 1～100 的整数。</p>
            ) : null}
          </div>
        ) : null}
        <label className="flex flex-col gap-2">
          <span className="text-sm font-medium text-fg">补充问题说明（选填）</span>
          <Textarea
            value={description}
            maxLength={20_000}
            rows={3}
            placeholder="描述遇到的问题和复现方式"
            onChange={(event) => {
              descriptionRevision.current += 1;
              setDescription(event.target.value);
            }}
            aria-label="问题说明"
          />
        </label>
        <div className="space-y-2 text-xs text-muted">
          <p>自动附带近期运行日志和相关故障记录。</p>
          {selectedIds.size > 0 ? (
            <p>所选轮次包含私人聊天、工具参数和结果，仅过滤可识别的认证凭据；分享前请检查私人内容。</p>
          ) : null}
          {extraOptions.length > 0 ? <p>高级选项已开启：{extraOptions.join("、")}。</p> : null}
          {includesScreenshots ? (
            <p className="text-warning">本次会包含完整桌面截图，可能有其他应用的私人内容；可在高级选项中查看或清除。</p>
          ) : null}
          {includeCrashDumps ? (
            <p className="text-warning">原始崩溃转储可能含聊天、凭据等内存内容，无法自动脱敏。</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-muted">
            <FileArchive className="size-4" />
            仅保存到本机下载目录，不会上传。
          </div>
          <Button
            loading={generating}
            disabled={selectedIds.size > 0 && !allTurns && !validTurnLimit}
            onClick={() => void generate()}
          >
            生成诊断包
          </Button>
        </div>

        {job ? (
          <div role="status" aria-live="polite" className="flex flex-col gap-2 text-sm text-muted">
            <p>{job.status === "cancelling" ? "正在取消，等待后台停止并清理…" : job.status === "cancelled" ? "诊断导出已取消" : job.status === "failed" ? "诊断导出失败" : diagnosticJobActive(job) ? `正在${({ preparing: "准备", "basic-state": "收集基础状态", "install-files": "校验安装文件", sessions: "收集会话", attachments: "收集附件", logs: "收集日志", "incident-evidence": "收集故障证据", archive: "生成 ZIP" } as Record<string, string>)[job.stage] ?? "处理"}…` : "导出处理已结束"}</p>
            <p>已用 {Math.floor(job.elapsedMs / 1000)} 秒{job.processedBytes > 0 ? ` · 本阶段已处理 ${(job.processedBytes / 1024 / 1024).toFixed(1)} MiB` : ""}</p>
            {job.selection ? <p>本次任务：{job.selection.logs === "all-retained" ? "全部已保留日志" : "近期日志"} · {job.selection.sessionCount} 个会话（{job.selection.sessionTurnLimit == null ? "全部轮次" : `最近 ${job.selection.sessionTurnLimit} 轮`}）{job.selection.includeAttachmentOriginals ? " · 含附件原件" : ""}{job.selection.includeCrashDumps ? " · 含原始崩溃转储" : ""}{job.selection.verifyInstallFiles ? " · 校验安装文件" : ""}</p> : null}
            {job.error || jobError ? <p role="alert" className="text-danger">{job.error || jobError}</p> : null}
            {job.status === "running" ? <Button variant="outline" size="sm" onClick={() => void cancelExport()}>取消导出</Button> : null}
          </div>
        ) : null}
        {result ? (
          <div className="flex flex-col gap-3 rounded-md border border-border bg-bg p-3">
            <div className="flex items-start gap-2 text-sm">
              {result.outcome === "complete" ? (
                <Check className="mt-0.5 size-4 shrink-0 text-success" />
              ) : (
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
              )}
              <div className="min-w-0 flex-1">
                <p className="font-medium text-fg">
                  {resultTitle(result)}
                </p>
                <p className="mt-1 break-all text-xs text-muted">{result.path}</p>
              </div>
            </div>
            {result.failures.length > 0 ? (
              <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
                {result.failures.map((failure, index) => (
                  <li key={`${failure.collection}-${failure.item ?? index}`}>
                    {failure.item ? `${failure.item}：` : ""}
                    {failure.message}
                  </li>
                ))}
              </ul>
            ) : null}
            {result.warnings?.length ? (
              <div className="text-xs text-muted">
                <p className="font-medium">{result.warnings.some((warning) => warning.collection === "temporary-files") ? "导出说明" : "历史证据说明"}</p>
                <ul className="mt-1 list-disc space-y-1 pl-5">
                  {result.warnings.map((warning, index) => (
                    <li key={`${warning.collection}-${warning.item ?? index}`}>
                      {warning.item ? `${warning.item}：` : ""}
                      {warning.message}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => void copyPath()}>
                <Copy className="size-4" />
                复制路径
              </Button>
              <Button variant="outline" size="sm" onClick={() => void reveal(result.path)}>
                <FolderOpen className="size-4" />
                打开所在目录
              </Button>
            </div>
          </div>
        ) : null}
      </SettingsGroup>
      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen} className="rounded-lg border border-border bg-surface/50">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" className="h-auto w-full justify-between p-4" aria-label="高级选项">
            <span>高级选项</span>
            <ChevronRight className={advancedOpen ? "size-4 rotate-90" : "size-4"} />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent forceMount hidden={!advancedOpen} className="flex flex-col gap-4 px-4 pb-4 data-[state=closed]:hidden">
          <p className="text-xs text-muted">仅在需要更多排查材料时调整，可能增加生成时间和文件大小。</p>
            <label className="flex items-start gap-3">
              <Checkbox checked={allRetainedLogs} disabled={generating} onCheckedChange={(value) => setAllRetainedLogs(value === true)} aria-label="收集全部已保留日志" />
              <span className="text-sm text-fg">收集全部已保留日志<span className="block text-xs text-muted">包括保留期内的旧日志，可能显著增加时间与大小；不改变会话范围或其它检查。{logEstimate == null ? "当前日志总量暂无法估计。" : `当前保留约 ${(logEstimate / 1024 / 1024).toFixed(1)} MiB（未压缩），生成时可能变化。`}</span></span>
            </label>
            <label className="flex items-start gap-3">
              <Checkbox checked={verifyInstallFiles} disabled={generating} onCheckedChange={(value) => setVerifyInstallFiles(value === true)} aria-label="校验安装文件完整性" />
              <span className="text-sm text-fg">校验安装文件完整性<span className="block text-xs text-muted">逐个读取安装文件。默认只报告身份和已有检查记录，本次校验标为未执行。</span></span>
            </label>
        {selectedIds.size > 0 ? (
          <label className="flex cursor-pointer select-none items-start gap-3 rounded-md border border-border p-3">
            <Checkbox
              className="mt-0.5"
              checked={includeAttachmentOriginals}
              disabled={generating}
              onCheckedChange={(checked) => setIncludeAttachmentOriginals(checked === true)}
              aria-label="包含相关原始附件"
            />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-fg">包含相关原始附件</span>
              <span className="mt-1 block text-xs text-muted">
                默认不包含。开启后仅收集本次采集轮次引用的附件原件，可能包含私人图片和文档，并会增大诊断包，不设导出体积上限。
              </span>
            </span>
          </label>
        ) : null}
        <label className="flex cursor-pointer select-none items-start gap-3 rounded-md border border-border p-3">
          <Checkbox
            className="mt-0.5"
            checked={includeCrashDumps}
            disabled={generating}
            onCheckedChange={(checked) => setIncludeCrashDumps(checked === true)}
            aria-label="包含原始崩溃转储"
          />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-medium text-fg">包含原始崩溃转储</span>
            <span className="mt-1 block text-xs text-muted">
              默认只导出崩溃摘要、记录覆盖情况和采集错误。原始转储可能包含聊天、凭据等内存内容，无法自动脱敏；仅在需要进一步定位时选择，发送前请按私人文件处理。
            </span>
          </span>
        </label>
          <p className="text-xs text-muted">
            默认普通日志每类最多读取 5 MiB、合计最多 32 MiB，不是整个 ZIP 的大小限制。会话按上方轮数采集，大小需处理后确定；已删除的历史无法补回。
          </p>
          <PetRenderEvidencePanel />
        {renderEvidenceStatus?.supported ? (
          <div className="flex flex-col gap-3 rounded-md border border-warning/40 bg-warning/10 p-3">
            <div className="flex items-start gap-3">
              <MonitorCog className="mt-0.5 size-4 shrink-0 text-warning" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">息屏黑屏取证</p>
                <p className="mt-1 text-xs text-muted">
                  显示器从息屏恢复时会自动保存最近一次桌宠 WebView2
                  内部画面和完整 Windows 桌面截图，无需提前开启。完整桌面可能包含其他应用和私人内容；证据只保留最近一次、本机保存、不会自动上传，只有你主动生成诊断包时才会导出。
                </p>
              </div>
            </div>

            <label className="flex cursor-pointer select-none items-start gap-3 rounded-md border border-border bg-bg p-3">
              <Checkbox
                className="mt-0.5"
                checked={renderEvidenceStatus.armed}
                disabled={renderEvidenceUpdating || renderEvidenceStatus.captureInProgress}
                onCheckedChange={(checked) =>
                  void updateRenderEvidenceArmed(checked === true)
                }
                aria-label="等待下一次息屏恢复时采集渲染证据"
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-fg">
                  额外等待下一次页面隐藏恢复时采集
                </span>
                <span className="mt-1 block text-xs text-muted">
                  {renderEvidenceStatus.waitingForVisibleAfterHidden
                    ? "已观察到页面隐藏，正在等待恢复。"
                    : renderEvidenceStatus.armed
                      ? "已开启一次性等待；保持 agent-friend 运行即可。"
                      : "这是手动补充入口；息屏恢复事故不需要开启。"}
                </span>
              </span>
            </label>

            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted">
                也可以立即采集一次，用于确认当前设备上的取证链路可用。
              </p>
              <Button
                variant="outline"
                size="sm"
                loading={
                  renderEvidenceUpdating || renderEvidenceStatus.captureInProgress
                }
                onClick={() => void captureRenderEvidence()}
              >
                立即采集
              </Button>
            </div>

            {renderEvidenceStatus.pending ? (
              <div className="flex flex-col gap-3 rounded-md border border-border bg-bg p-3">
                <div className="flex items-start gap-2">
                  {renderEvidenceStatus.pending.complete ? (
                    <Check className="mt-0.5 size-4 shrink-0 text-success" />
                  ) : (
                    <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-fg">最近一次渲染证据已保存</p>
                    <p className="mt-1 text-xs text-muted">
                      {dayjs(renderEvidenceStatus.pending.capturedAt).format(
                        "YYYY-MM-DD HH:mm:ss",
                      )}
                      {renderEvidenceStatus.pending.complete
                        ? " · 两侧画面完整"
                        : ` · ${renderEvidenceStatus.pending.errorCount} 项采集失败`}
                    </p>
                  </div>
                </div>
                <label className="flex cursor-pointer select-none items-start gap-3">
                  <Checkbox
                    className="mt-0.5"
                    checked={
                      renderEvidenceStatus.pending.automaticIncident ||
                      includeRenderEvidence
                    }
                    disabled={generating || renderEvidenceStatus.pending.automaticIncident}
                    onCheckedChange={(checked) =>
                      setIncludeRenderEvidence(checked === true)
                    }
                    aria-label="在诊断包中包含最近一次渲染证据"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-fg">
                      {renderEvidenceStatus.pending.automaticIncident
                        ? "显示恢复事故证据将自动包含"
                        : "在本次 ZIP 中包含渲染截图"}
                    </span>
                    <span className="mt-1 block text-xs text-muted">
                      {renderEvidenceStatus.pending.automaticIncident
                        ? "你只需生成诊断包；ZIP 会包含这次自动取证。发送前仍请按私人文件检查。"
                        : "再次确认：ZIP 将包含完整桌面截图和桌宠 WebView2 内部截图，发送前请按私人文件检查。"}
                    </span>
                  </span>
                </label>
                <div className="flex justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={renderEvidenceUpdating}
                    onClick={() => void clearPendingRenderEvidence()}
                  >
                    <Trash2 className="size-4" />
                    清除本机证据
                  </Button>
                </div>
              </div>
            ) : null}

            {renderEvidenceStatus.storageError ? (
              <p className="text-xs text-danger">
                本机渲染证据状态不可读：{renderEvidenceStatus.storageError}
              </p>
            ) : null}
          </div>
        ) : null}

        </CollapsibleContent>
      </Collapsible>
    </SettingsPageLayout>
  );
}
