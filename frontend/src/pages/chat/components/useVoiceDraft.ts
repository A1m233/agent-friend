import { useCallback, useEffect, useState, type RefObject } from "react";
import { toast } from "sonner";
import { useDraftStore } from "@/stores/drafts";
import { useVoiceInputStore } from "@/stores/voiceInput";
import { isVoiceInputLive } from "@/stores/voiceInputStateMachine";
import type { VoiceDraftUpdate } from "@/services/voiceInput/draftSession";
import type { ChatSenderEditorState } from "./chatSenderEditor";

export function useVoiceDraft(
  workspaceKey: string,
  isWorkspaceWritable: (key: string) => boolean,
  draftTextRef: RefObject<string>,
  shouldIgnoreEdit: (text: string) => boolean,
) {
  const latestVoiceTranscript = useVoiceInputStore((s) => s.latestTranscript);
  const acknowledgeVoiceTranscript = useVoiceInputStore((s) => s.acknowledgeTranscript);
  const stopVoiceInput = useVoiceInputStore((s) => s.stop);
  const setWorkspaceText = useDraftStore((s) => s.setText);
  const [voiceSelection, setVoiceSelection] = useState<{ text: string; caret: number } | null>(null);

  useEffect(() => {
    setVoiceSelection(null);
    const state = useVoiceInputStore.getState();
    if (state.draftSession && state.draftSession.workspaceKey !== workspaceKey) {
      state.draftSession.close();
      if (isVoiceInputLive(state.phase)) void stopVoiceInput("manual");
    }
  }, [stopVoiceInput, workspaceKey]);

  const handleTextChange = useCallback((text: string) => {
    if (!isWorkspaceWritable(workspaceKey) || shouldIgnoreEdit(text) || draftTextRef.current === text) return;
    const session = useVoiceInputStore.getState().draftSession;
    if (session?.workspaceKey === workspaceKey && text !== session.text) session.edit(text, text.length);
    draftTextRef.current = text;
    setWorkspaceText(workspaceKey, text);
    setVoiceSelection(null);
  }, [draftTextRef, isWorkspaceWritable, setWorkspaceText, shouldIgnoreEdit, workspaceKey]);
  const applyVoiceUpdate = useCallback((update: VoiceDraftUpdate) => {
    if (!isWorkspaceWritable(workspaceKey)) return;
    if (update.conflict) {
      toast.error("语音续写无法对齐，已保留你的修改，请重新开启语音输入继续。");
      void stopVoiceInput("manual");
      return;
    }
    draftTextRef.current = update.text;
    setWorkspaceText(workspaceKey, update.text);
    setVoiceSelection({ text: update.text, caret: update.caret });
  }, [draftTextRef, isWorkspaceWritable, setWorkspaceText, stopVoiceInput, workspaceKey]);

  const handleEditorChange = useCallback((editor: ChatSenderEditorState) => {
    if (!isWorkspaceWritable(workspaceKey) || shouldIgnoreEdit(editor.text)) return;
    const session = useVoiceInputStore.getState().draftSession;
    if (session?.workspaceKey !== workspaceKey) {
      draftTextRef.current = editor.text;
      setWorkspaceText(workspaceKey, editor.text);
      return;
    }
    session.edit(editor.text, editor.caret, editor.composing);
    draftTextRef.current = editor.text;
    setWorkspaceText(workspaceKey, editor.text);
    // Clear the previous voice caret before a user edit renders.
    setVoiceSelection(null);
    if (!editor.composing) {
      const update = session.flush();
      if (update) applyVoiceUpdate(update);
    }
  }, [applyVoiceUpdate, draftTextRef, isWorkspaceWritable, setWorkspaceText, shouldIgnoreEdit, workspaceKey]);

  useEffect(() => {
    if (!latestVoiceTranscript) return;
    const session = useVoiceInputStore.getState().draftSession;
    if (session && session.workspaceKey === workspaceKey) {
      const currentText = useDraftStore.getState().workspaces[workspaceKey]?.text ?? "";
      if (currentText !== session.text) session.edit(currentText, currentText.length);
      applyVoiceUpdate(session.apply(latestVoiceTranscript));
    }
    acknowledgeVoiceTranscript(latestVoiceTranscript.seq);
  }, [acknowledgeVoiceTranscript, applyVoiceUpdate, latestVoiceTranscript, workspaceKey]);

  return { voiceSelection, handleEditorChange, handleTextChange };
}
