import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, FolderOpen, LifeBuoy, TriangleAlert } from "lucide-react";
import dayjs from "dayjs";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { toast } from "sonner";

import { Button, Checkbox, ScrollArea, Textarea } from "@/components/ui";
import {
  generateDiagnosticBundle,
  type GenerateDiagnosticBundleResult,
} from "@/services";
import { useArchivedSessionsStore, useSessionsStore } from "@/stores";
import type { SessionSummary } from "@/types/meta";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsPageLayout } from "./SettingsPageLayout";

export interface FeedbackDiagnosticsPrefill {
  requestId: number;
  sessionId?: string;
}

export function FeedbackDiagnosticsPanel({
  prefill,
}: {
  prefill: FeedbackDiagnosticsPrefill | null;
}) {
  const sessions = useSessionsStore((state) => state.list);
  const sessionsLoading = useSessionsStore((state) => state.loading);
  const refreshSessions = useSessionsStore((state) => state.refresh);
  const archivedSessions = useArchivedSessionsStore((state) => state.list);
  const archivedLoading = useArchivedSessionsStore((state) => state.loading);
  const refreshArchived = useArchivedSessionsStore((state) => state.refresh);
  const [description, setDescription] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<GenerateDiagnosticBundleResult | null>(null);
  const appliedPrefillRequestId = useRef(0);

  useEffect(() => {
    void Promise.all([refreshSessions(), refreshArchived()]);
  }, [refreshArchived, refreshSessions]);

  const combinedSessions = useMemo(() => {
    const byId = new Map<string, SessionSummary>();
    for (const session of [...sessions, ...archivedSessions]) {
      byId.set(session.session_id, session);
    }
    return [...byId.values()].sort((a, b) =>
      b.updated_at.localeCompare(a.updated_at),
    );
  }, [archivedSessions, sessions]);

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
    setGenerating(true);
    setResult(null);
    try {
      const next = await generateDiagnosticBundle({
        description: description.trim() || undefined,
        sessionIds: [...selectedIds],
      });
      setResult(next);
      const partial = next.outcome === "partial";
      const notify = partial ? toast.warning : toast.success;
      notify(partial ? "诊断包已生成，但部分内容获取失败" : "诊断包已生成", {
        action: {
          label: "打开所在目录",
          onClick: () => void reveal(next.path),
        },
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      toast.error("无法生成诊断包", { description: message });
    } finally {
      setGenerating(false);
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

  return (
    <SettingsPageLayout title="帮助与反馈">
      <SettingsGroup title="问题说明（选填）">
        <Textarea
          value={description}
          maxLength={20_000}
          rows={5}
          placeholder="可以描述实际表现、期望表现和复现方式"
          onChange={(event) => setDescription(event.target.value)}
          aria-label="问题说明"
        />
        <p className="text-xs text-muted">不填写也可以生成基础诊断包。</p>
      </SettingsGroup>

      <SettingsGroup title="相关会话（选填）">
        <div className="flex gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-fg">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          <p>
            选中的完整会话会包含私人聊天、工具调用参数和结果。应用只过滤可识别的认证凭据，不会审查或删除一般私人内容，请仅选择与问题相关的会话。
          </p>
        </div>
        <ScrollArea className="h-72 rounded-md border border-border">
          <div className="flex flex-col p-2">
            {sessionsLoading || archivedLoading ? (
              <p className="p-3 text-sm text-muted">正在加载会话…</p>
            ) : combinedSessions.length === 0 ? (
              <p className="p-3 text-sm text-muted">暂无可导出的已持久化会话</p>
            ) : (
              combinedSessions.map((session) => {
                const archived = Boolean(session.archived_at);
                return (
                  <label
                    key={session.session_id}
                    className="flex cursor-pointer items-start gap-3 rounded-md p-3 hover:bg-surface-hover"
                  >
                    <Checkbox
                      className="mt-0.5"
                      checked={selectedIds.has(session.session_id)}
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
        <p className="text-xs text-muted">已选择 {selectedIds.size} 个会话，最多 20 个。</p>
      </SettingsGroup>

      <SettingsGroup title="生成本地诊断包">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-muted">
            <LifeBuoy className="size-4" />
            ZIP 只保存在本机下载目录，不会自动上传。
          </div>
          <Button
            loading={generating}
            disabled={selectedIds.size > 20}
            onClick={() => void generate()}
          >
            生成诊断包
          </Button>
        </div>

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
                  {result.outcome === "complete"
                    ? "诊断包已生成"
                    : "诊断包已生成，但部分内容获取失败"}
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
    </SettingsPageLayout>
  );
}
