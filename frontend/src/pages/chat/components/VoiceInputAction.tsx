import { LoaderCircle, Mic, Square } from "lucide-react";

import { TooltipButton } from "@/components/ui";
import { MicActivityFrame } from "@/components/voice/MicActivityFrame";
import { voiceVolumeLabel } from "@/components/voice/MicLevelBars";
import { hasMicActivity } from "@/components/voice/micActivity";
import type { VoiceInputPhase } from "@/services/voiceInput/types";
import { cn } from "@/utils/cn";
import {
  canStopVoiceInput,
  isVoiceInputLive,
  voiceInputActionLabel,
  voiceInputStatusLabel,
} from "@/stores/voiceInputStateMachine";

interface VoiceInputActionProps {
  phase: VoiceInputPhase;
  volumeLevel: number;
  error: string | null;
  disabled?: boolean;
  onToggle: () => void;
}

export function VoiceInputAction({
  phase,
  volumeLevel,
  error,
  disabled = false,
  onToggle,
}: VoiceInputActionProps) {
  const live = isVoiceInputLive(phase);
  const stopEnabled = canStopVoiceInput(phase);
  const busy = phase === "requesting_microphone" || phase === "stopping";
  const receivingVoice = phase === "recording" && hasMicActivity(volumeLevel);
  const tooltip = error ?? (live ? voiceInputStatusLabel(phase, volumeLevel) : voiceInputActionLabel(phase));
  const ariaLabel = live ? "停止语音输入" : "开始语音输入";

  return (
    <span className="flex items-center">
      <MicActivityFrame active={phase === "recording"} level={volumeLevel} size="sm">
        <TooltipButton
          type="button"
          icon={
            busy ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : live ? (
              <Square className="size-4" />
            ) : (
              <Mic className="size-4" />
            )
          }
          tooltip={tooltip || voiceVolumeLabel(volumeLevel)}
          aria-label={ariaLabel}
          tooltipSide="top"
          variant="ghost"
          size="icon-sm"
          disabled={disabled || (live && !stopEnabled)}
          className={cn(
            "relative rounded-full text-muted hover:text-fg",
            receivingVoice && "text-success hover:text-success",
            phase === "error" && "text-danger hover:text-danger",
          )}
          onClick={onToggle}
        />
      </MicActivityFrame>
    </span>
  );
}
