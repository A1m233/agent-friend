import { EventType, type BaseEvent } from "@ag-ui/core";
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "@/types/chat";
import {
  personaCandidateFromEvent,
  visiblePersonaEditorMessages,
} from "./personaEditorState";

describe("persona editor projection", () => {
  it("extracts a candidate from the proposal tool result", () => {
    const event = {
      type: EventType.TOOL_CALL_RESULT,
      toolCallId: "proposal-1",
      messageId: "message-1",
      content: JSON.stringify({
        kind: "persona_candidate",
        content: "# Echo",
        displayName: "Echo",
        summary: ["更直接"],
        baseRevision: "sha256:base",
      }),
    } as BaseEvent;

    expect(personaCandidateFromEvent(event)).toEqual({
      kind: "persona_candidate",
      content: "# Echo",
      displayName: "Echo",
      summary: ["更直接"],
      baseRevision: "sha256:base",
    });
  });

  it("hides proposal tool cards while retaining normal chat content", () => {
    const message: ChatMessage = {
      id: "assistant-1",
      role: "assistant",
      createdAt: "2026-07-16T00:00:00Z",
      status: "complete",
      blocks: [
        {
          kind: "tool",
          toolCallId: "proposal-1",
          name: "propose_persona",
          args: "{}",
          result: "{}",
          status: "done",
        },
        { kind: "text", mid: "text-1", text: "候选版本已经准备好了。" },
      ],
    };

    expect(visiblePersonaEditorMessages([message])[0].blocks).toEqual([
      { kind: "text", mid: "text-1", text: "候选版本已经准备好了。" },
    ]);
  });
});
