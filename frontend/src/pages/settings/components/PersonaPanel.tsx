import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileText, FolderOpen, MoreHorizontal, RotateCcw, Undo2 } from "lucide-react";
import {
  Button,
  ConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { ChatSenderBox } from "@/pages/chat/components/ChatSenderBox";
import { MessageTimeline } from "@/pages/chat/components/MessageList";
import {
  openPersonaTarget,
  personaApi,
  runPersonaEditor,
  type PersonaCandidate,
  type PersonaState,
} from "@/services";
import { applyAguiEvent } from "@/stores/conversationReducer";
import { projectSessionEvents } from "@/stores/sessionProjection";
import type { ChatMessage } from "@/types/chat";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { buildPersonaDiff, type PersonaDiffLine } from "../personaDraftState";
import {
  personaCandidateFromEvent,
  visiblePersonaEditorMessages,
} from "../personaEditorState";
import { PersonaMarkdown } from "./PersonaMarkdown";
import { SettingsPageLayout } from "./SettingsPageLayout";

const PERSONA_REFRESH_MS = 3_000;

interface PersonaPanelProps {
  active: boolean;
}

type VersionView = "current" | "candidate" | "diff";

function errorMessage(error: unknown): string {
  if (error && typeof error === "object" && "friendly" in error) {
    const friendly = (error as { friendly?: unknown }).friendly;
    if (typeof friendly === "string") return friendly;
  }
  return "这次操作没有完成，请稍后重试。";
}

function userMessage(text: string): ChatMessage {
  const id = crypto.randomUUID();
  return {
    id,
    role: "user",
    createdAt: new Date().toISOString(),
    blocks: [{ kind: "text", mid: id, text }],
    status: "complete",
  };
}

function emptyAssistantMessage(): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role: "assistant",
    createdAt: new Date().toISOString(),
    blocks: [],
    status: "streaming",
  };
}

function DiffLine({ line }: { line: PersonaDiffLine }) {
  const marker = line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " ";
  const tone = line.kind === "added"
    ? "bg-success/10 text-fg"
    : line.kind === "removed"
      ? "bg-danger/10 text-fg"
      : "text-muted";
  return (
    <div className={`flex min-w-0 gap-2 px-3 py-0.5 ${tone}`}>
      <span className="w-4 shrink-0 select-none text-center text-muted">{marker}</span>
      <span className="min-w-0 whitespace-pre-wrap break-words">{line.text || " "}</span>
    </div>
  );
}

export function PersonaPanel({ active }: PersonaPanelProps) {
  const [persona, setPersona] = useState<PersonaState | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [editorMessages, setEditorMessages] = useState<ChatMessage[]>([]);
  const [candidate, setCandidate] = useState<PersonaCandidate | null>(null);
  const [editorText, setEditorText] = useState("");
  const [editorStreaming, setEditorStreaming] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [versionView, setVersionView] = useState<VersionView>("current");
  const [advancedTarget, setAdvancedTarget] = useState<"file" | "folder" | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const editorAbort = useRef<AbortController | null>(null);

  const conflict = Boolean(candidate && persona && candidate.baseRevision !== persona.revision);
  const currentDisplayName = persona?.displayName ?? "你的朋友";
  const candidateDisplayName = candidate?.displayName ?? currentDisplayName;
  const displayedName = versionView === "current" ? currentDisplayName : candidateDisplayName;
  const editorDisplayName = candidateDisplayName;
  const diffLines = useMemo(
    () => buildPersonaDiff(persona?.content ?? "", candidate?.content ?? ""),
    [candidate?.content, persona?.content],
  );

  const loadWorkspace = useCallback(async () => {
    try {
      const [nextPersona, editor] = await Promise.all([personaApi.get(), personaApi.editor()]);
      setPersona(nextPersona);
      setThreadId(editor.threadId);
      setEditorMessages(projectSessionEvents(editor.events));
      setCandidate(editor.candidate);
      if (editor.candidate) setVersionView("candidate");
    } catch (nextError) {
      setError(errorMessage(nextError));
    }
  }, []);

  const refreshPersona = useCallback(async () => {
    try {
      setPersona(await personaApi.get());
    } catch (nextError) {
      setError(errorMessage(nextError));
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void loadWorkspace();
    const onFocus = () => void refreshPersona();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshPersona();
    }, PERSONA_REFRESH_MS);
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [active, loadWorkspace, refreshPersona]);

  const stopEditor = useCallback(() => {
    editorAbort.current?.abort();
  }, []);

  useEffect(() => () => stopEditor(), [stopEditor]);

  const sendEditorMessage = async (rawText: string) => {
    const text = rawText.trim();
    if (!text || !threadId || editorStreaming) return;
    const controller = new AbortController();
    editorAbort.current = controller;
    const user = userMessage(text);
    let assistant = emptyAssistantMessage();
    setEditorText("");
    setError(null);
    setEditorStreaming(true);
    setEditorMessages((messages) => [...messages, user, assistant]);
    try {
      for await (const event of runPersonaEditor(threadId, text, controller.signal)) {
        const nextCandidate = personaCandidateFromEvent(event);
        if (nextCandidate) {
          setCandidate(nextCandidate);
          setVersionView("candidate");
        }
        assistant = applyAguiEvent(assistant, event);
        setEditorMessages((messages) =>
          messages.map((message) => (message.id === assistant.id ? assistant : message)),
        );
      }
      assistant = assistant.status === "streaming"
        ? { ...assistant, status: "complete" }
        : assistant;
      setEditorMessages((messages) =>
        messages.map((message) => (message.id === assistant.id ? assistant : message)),
      );
    } catch {
      assistant = controller.signal.aborted
        ? { ...assistant, status: "interrupted" }
        : {
            ...assistant,
            status: "error",
            error: "这轮人格调整没有完成，可以再试一次。",
          };
      setEditorMessages((messages) =>
        messages.map((message) => (message.id === assistant.id ? assistant : message)),
      );
    } finally {
      if (editorAbort.current === controller) editorAbort.current = null;
      setEditorStreaming(false);
    }
  };

  const discardEditor = async (): Promise<boolean> => {
    if (editorStreaming) stopEditor();
    setActionLoading(true);
    setError(null);
    try {
      const snapshot = await personaApi.discardEditor();
      setThreadId(snapshot.threadId);
      setEditorMessages([]);
      setCandidate(null);
      setVersionView("current");
      return true;
    } catch (nextError) {
      setError(errorMessage(nextError));
      return false;
    } finally {
      setActionLoading(false);
    }
  };

  const applyCandidate = async () => {
    if (!candidate || conflict || actionLoading) return;
    setActionLoading(true);
    setError(null);
    try {
      const next = await personaApi.apply(candidate.content, candidate.baseRevision);
      setPersona(next);
      setEditorMessages([]);
      setCandidate(null);
      setVersionView("current");
    } catch (nextError) {
      setError(errorMessage(nextError));
      void refreshPersona();
    } finally {
      setActionLoading(false);
    }
  };

  const openAdvanced = async (target: "file" | "folder") => {
    if (editorMessages.length > 0 || candidate) {
      setAdvancedTarget(target);
      return;
    }
    try {
      await openPersonaTarget(target);
      await refreshPersona();
    } catch (nextError) {
      setError(errorMessage(nextError));
    }
  };

  const undoApplied = async () => {
    setActionLoading(true);
    setError(null);
    try {
      setPersona(await personaApi.undo());
      setEditorMessages([]);
      setCandidate(null);
      setVersionView("current");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionLoading(false);
    }
  };

  const reset = async () => {
    setActionLoading(true);
    setError(null);
    try {
      setPersona(await personaApi.reset());
      setEditorMessages([]);
      setCandidate(null);
      setVersionView("current");
      setConfirmReset(false);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionLoading(false);
    }
  };

  const displayedContent = versionView === "candidate" && candidate
    ? candidate.content
    : persona?.content ?? "";

  return (
    <div className={active ? "contents" : "hidden"} aria-hidden={!active}>
      <SettingsPageLayout
        title="人格"
        actions={(
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="更多人格操作">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              <DropdownMenuItem onSelect={() => void openAdvanced("file")}>
                <FileText className="size-4" />
                用外部应用编辑
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void openAdvanced("folder")}>
                <FolderOpen className="size-4" />
                打开所在文件夹
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setConfirmReset(true)}>
                <RotateCcw className="size-4" />
                恢复初始人格
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      >
        <div className="overflow-hidden rounded-xl border border-border bg-surface/50">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="font-medium">人格约束</h3>
              </div>
              <p className="mt-1 text-sm text-muted">这里的内容决定 {displayedName} 如何与你相处和回应。</p>
            </div>
            {persona?.canUndoApply ? (
              <Button size="sm" variant="ghost" disabled={actionLoading} onClick={() => void undoApplied()}>
                <Undo2 className="size-4" />
                撤销刚才的应用
              </Button>
            ) : null}
          </div>

          {persona?.diagnostic ? (
            <div className="mx-5 mt-4 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
              {persona.diagnostic.message} 当前展示并使用最后一个有效版本。
            </div>
          ) : null}
          {error ? (
            <div className="mx-5 mt-4 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger" role="alert">
              {error}
            </div>
          ) : null}

          <Tabs value={versionView} onValueChange={(value) => setVersionView(value as VersionView)} className="gap-0">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
              <TabsList>
                <TabsTrigger value="current">当前版本</TabsTrigger>
                <TabsTrigger value="candidate" disabled={!candidate}>候选版本</TabsTrigger>
                <TabsTrigger value="diff" disabled={!candidate}>查看变化</TabsTrigger>
              </TabsList>
              {candidate ? (
                <div className="flex items-center gap-2">
                  <Button variant="ghost" size="sm" disabled={actionLoading} onClick={() => void discardEditor()}>
                    放弃修改
                  </Button>
                  <Button size="sm" loading={actionLoading} disabled={conflict} onClick={() => void applyCandidate()}>
                    应用修改
                  </Button>
                </div>
              ) : null}
            </div>

            {candidate && versionView !== "current" ? (
              <div className="border-b border-border bg-surface/50 px-5 py-3">
                <p className="text-xs font-medium text-muted">本次主要变化</p>
                <ul className="mt-2 flex flex-col gap-1 text-sm">
                  {candidate.summary.map((item) => (
                    <li key={item} className="flex gap-2"><span className="text-muted">·</span><span>{item}</span></li>
                  ))}
                </ul>
              </div>
            ) : null}

            {conflict ? (
              <div className="mx-5 mt-4 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
                persona.md 已在外部发生变化。请放弃候选修改，再基于新版本继续调整。
              </div>
            ) : null}

            <TabsContent value="current" className="m-0 max-h-80 flex-none overflow-y-auto bg-bg px-5 py-5">
              {persona ? <PersonaMarkdown content={persona.content} /> : <p className="py-16 text-center text-sm text-muted">正在读取人格约束…</p>}
            </TabsContent>
            <TabsContent value="candidate" className="m-0 max-h-80 flex-none overflow-y-auto bg-bg px-5 py-5">
              {candidate ? <PersonaMarkdown content={displayedContent} /> : null}
            </TabsContent>
            <TabsContent value="diff" className="m-0 max-h-80 flex-none overflow-y-auto bg-bg px-5 py-5">
              {candidate ? (
                <div className="overflow-hidden rounded-lg border border-border bg-surface font-mono text-xs leading-5">
                  {diffLines.map((line, index) => <DiffLine key={`${line.kind}:${index}:${line.text}`} line={line} />)}
                </div>
              ) : null}
            </TabsContent>
          </Tabs>

          <div className="border-t border-border bg-bg">
            <div className="border-b border-border px-5 py-3">
              <h4 className="text-sm font-medium">和 {editorDisplayName} 一起调整</h4>
              <p className="mt-1 text-xs text-muted">
                像聊天一样描述你想要的变化；必要时会继续询问，并在准备好后生成完整候选版本。
              </p>
            </div>
            <div className="flex h-80 flex-col">
              <MessageTimeline
                messages={visiblePersonaEditorMessages(editorMessages)}
                streaming={editorStreaming}
                overlayComposer={false}
                emptyLabel={`告诉 ${editorDisplayName} 你希望它如何与你相处`}
              />
              <div className="border-t border-border bg-surface/50 p-3">
                <ChatSenderBox
                  placement="bottom"
                  value={editorText}
                  disabled={!threadId}
                  loading={editorStreaming}
                  placeholder="例如：少一点说教，先接住我的情绪"
                  onNativeValueChange={setEditorText}
                  onChange={(event) => {
                    const value = readStringEventValue(event);
                    if (value !== null) setEditorText(value);
                  }}
                  onSend={(event) => void sendEditorMessage(event.detail.value || editorText)}
                  onStop={stopEditor}
                />
              </div>
            </div>
            <p className="border-t border-border px-5 py-3 text-xs text-muted">
              应用后的修改从下一个新对话回合开始生效，不会改写聊天记录。
            </p>
          </div>
        </div>
      </SettingsPageLayout>

      <ConfirmDialog
        open={advancedTarget !== null}
        title="放弃人格编辑对话？"
        description="外部编辑不会自动合并当前编辑对话。继续后会先清空对话和候选修改。"
        confirmLabel="放弃并打开"
        onCancel={() => setAdvancedTarget(null)}
        onConfirm={() => {
          const target = advancedTarget;
          setAdvancedTarget(null);
          void discardEditor().then((discarded) => {
            if (!discarded || !target) return undefined;
            return openPersonaTarget(target).then(refreshPersona);
          }).catch((nextError) => setError(errorMessage(nextError)));
        }}
      />
      <ConfirmDialog
        open={confirmReset}
        title="恢复初始人格？"
        description="这会用随应用发布的初始内容覆盖 persona.md，并清空人格编辑对话。"
        confirmLabel="恢复初始人格"
        variant="destructive"
        loading={actionLoading}
        onCancel={() => setConfirmReset(false)}
        onConfirm={() => void reset()}
      />
    </div>
  );
}
