// @vitest-environment jsdom
import { act, createElement, useRef } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceDraftSession } from "@/services/voiceInput/draftSession";
import { useVoiceInputStore } from "@/stores/voiceInput";
import { useDraftStore } from "@/stores/drafts";
import { ChatSenderBox } from "./ChatSenderBox";
import { findSenderTextarea } from "./chatSenderEditor";
import { useVoiceDraft } from "./useVoiceDraft";
import { readStringEventValue } from "@/utils/webComponentEvents";
import { toast } from "sonner";

vi.hoisted(() => {
  Object.defineProperty(document, "adoptedStyleSheets", { value: [], configurable: true });
  CSSStyleSheet.prototype.replaceSync = () => {};
});
vi.mock("@/services", () => ({ releaseAttachmentDraft: vi.fn() }));
vi.mock("@/lib/permissions", () => ({
  hasPermissions: () => true, refreshPermissions: vi.fn(), subscribePermissionSnapshot: vi.fn(),
}));
vi.mock("@/lib/permissions/featureGuard", () => ({ waitForFeaturePermissions: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const writable = () => true;
const ignore = () => false;
function Input({ workspace = "chat" }: { workspace?: string }) {
  const text = useDraftStore((s) => s.workspaces[workspace]?.text ?? "");
  const textRef = useRef(text);
  const voice = useVoiceDraft(workspace, writable, textRef, ignore);
  return createElement(ChatSenderBox, {
    placement: "bottom", value: text,
    onNativeEditorChange: voice.handleEditorChange, voiceSelection: voice.voiceSelection,
    onNativeValueChange: voice.handleTextChange,
    onChange: (e) => {
      const value = readStringEventValue(e);
      if (value !== null) voice.handleTextChange(value);
    },
  });
}

describe("real ChatSender, draft store and voice event consumption", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useDraftStore.setState({ workspaces: {} });
    useVoiceInputStore.setState({
      phase: "recording", traceId: "trace", targetWorkspaceKey: "chat", latestTranscript: null,
      draftSession: new VoiceDraftSession("chat", "", 0), stop: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("closes the old draft on workspace change and never replays a late final", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const emit = async (text: string, seq: number) => act(async () => {
      useVoiceInputStore.setState({ latestTranscript: {
        text, seq, traceId: "trace", targetWorkspaceKey: "chat", kind: "partial",
      } });
    });
    try {
      await act(async () => root.render(createElement(Input)));
      await emit("旧草稿", 1);
      await act(async () => root.render(createElement(Input, { workspace: "other" })));
      await emit("旧草稿迟到", 2);
      expect(findSenderTextarea(container)!.value).toBe("");
      expect(useVoiceInputStore.getState().stop).toHaveBeenCalledWith("manual");
      await act(async () => root.render(createElement(Input)));
      await emit("旧草稿再次迟到", 3);
      expect(findSenderTextarea(container)!.value).toBe("旧草稿");
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it("keeps the edited draft and reports an unalignable revision once", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const emit = async (text: string, seq: number) => act(async () => {
      useVoiceInputStore.setState({ latestTranscript: {
        text, seq, traceId: "trace", targetWorkspaceKey: "chat", kind: "partial",
      } });
    });
    try {
      await act(async () => root.render(createElement(Input)));
      await emit("一百个", 1);
      const textarea = findSenderTextarea(container)!;
      await act(async () => {
        textarea.value = "两百个";
        textarea.setSelectionRange(3, 3);
        textarea.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true }));
      });
      await emit("100个新的", 2);
      await emit("100个新的迟到", 3);
      expect(textarea.value).toBe("两百个");
      expect(toast.error).toHaveBeenCalledOnce();
      expect(useVoiceInputStore.getState().stop).toHaveBeenCalledOnce();
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it("keeps manual corrections after acknowledgement, remount and continuation", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const emit = async (text: string, seq: number) => act(async () => {
      useVoiceInputStore.setState({ latestTranscript: {
        text, seq, traceId: "trace", targetWorkspaceKey: "chat", kind: "partial",
      } });
    });
    try {
      await act(async () => root.render(createElement(Input)));
      await emit("今天天气很好", 1);
      let textarea = findSenderTextarea(container)!;
      expect(textarea.value).toBe("今天天气很好");
      expect(textarea.selectionStart).toBe(6);
      expect(useVoiceInputStore.getState().latestTranscript).toBeNull();
      await act(async () => {
        textarea.value = "今天天气不错";
        textarea.setSelectionRange(6, 6);
        textarea.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true }));
      });
      await act(async () => root.render(null));
      await act(async () => root.render(createElement(Input)));
      await emit("今天天气很好我们出去走走", 2);
      textarea = findSenderTextarea(container)!;
      expect(textarea.value).toBe("今天天气不错我们出去走走");
      expect(useDraftStore.getState().workspaces.chat.text).toBe(textarea.value);
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it("preserves the insertion point and defers updates until IME commits", async () => {
    useDraftStore.getState().setText("chat", "前后");
    useVoiceInputStore.setState({ draftSession: new VoiceDraftSession("chat", "前后", 1) });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const emit = async (text: string, seq: number) => act(async () => {
      useVoiceInputStore.setState({ latestTranscript: {
        text, seq, traceId: "trace", targetWorkspaceKey: "chat", kind: "partial",
      } });
    });
    try {
      await act(async () => root.render(createElement(Input)));
      await emit("今天", 1);
      const textarea = findSenderTextarea(container)!;
      expect(textarea.value).toBe("前今天后");
      expect(textarea.selectionStart).toBe(3);
      await act(async () => {
        textarea.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true, composed: true }));
        textarea.value = "前今天hao后";
        textarea.setSelectionRange(6, 6);
        textarea.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, isComposing: true }));
      });
      await emit("今天下雨", 2);
      expect(textarea.value).toBe("前今天hao后");
      await act(async () => {
        textarea.value = "前今天好后";
        textarea.setSelectionRange(4, 4);
        textarea.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, composed: true }));
      });
      expect(textarea.value).toBe("前今天好下雨后");
      expect(textarea.selectionStart).toBe(6);
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});
