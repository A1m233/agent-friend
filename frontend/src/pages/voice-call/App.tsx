import { Mic, MicOff, Phone, PhoneOff } from "lucide-react";

import { MicActivityFrame } from "@/components/voice/MicActivityFrame";
import { VoiceTunnelConsentDialog } from "@/components/voice/VoiceTunnelConsentDialog";
import { MicLevelBars, voiceVolumeLabel } from "@/components/voice/MicLevelBars";
import { hasMicActivity } from "@/components/voice/micActivity";
import { Button, TooltipButton, TooltipProvider } from "@/components/ui";
import { useVoiceStore } from "@/stores/voice";
import {
  isVoiceCallLive,
  voiceFailureStageLabel,
  voicePhaseLabel,
} from "@/stores/voiceStateMachine";

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

export function VoiceCallApp() {
  const phase = useVoiceStore((s) => s.phase);
  const callId = useVoiceStore((s) => s.callId);
  const isOwner = useVoiceStore((s) => s.isOwner);
  const durationMs = useVoiceStore((s) => s.durationMs);
  const volumeLevel = useVoiceStore((s) => s.volumeLevel);
  const muted = useVoiceStore((s) => s.muted);
  const error = useVoiceStore((s) => s.error);
  const diagnostic = useVoiceStore((s) => s.diagnostic);
  const requestStart = useVoiceStore((s) => s.requestStart);
  const requestConfirmTunnel = useVoiceStore((s) => s.requestConfirmTunnelConsent);
  const requestCancelTunnel = useVoiceStore((s) => s.requestCancelTunnelConsent);
  const requestHangUp = useVoiceStore((s) => s.requestHangUp);
  const requestToggleMute = useVoiceStore((s) => s.requestToggleMute);

  const awaitingConsent = phase === "confirming_tunnel";
  const canDial = phase === "idle" || phase === "ended" || phase === "error";
  const inCall = phase === "active";
  const busy = isVoiceCallLive(phase) && phase !== "active";
  const canToggleMute = phase === "connecting_agent" || phase === "starting_agent" || phase === "active";
  const micWaveActive = canToggleMute && hasMicActivity(volumeLevel, muted);

  const handleToggleMute = () => {
    console.info("[voice-call][button] toggle-mute click", {
      phase,
      callId,
      isOwner,
      muted,
      canToggleMute,
      volumeLevel,
    });
    void requestToggleMute();
  };

  const handleHangUp = () => {
    console.info("[voice-call][button] hangup click", {
      phase,
      callId,
      isOwner,
      muted,
      volumeLevel,
    });
    void requestHangUp();
  };

  return (
    <TooltipProvider delayDuration={0}>
      <main className="flex h-screen flex-col bg-bg text-fg">
        <header className="flex items-center border-b border-border px-4 py-3">
          <span className="text-sm font-medium text-muted">语音通话</span>
        </header>

        <section className="flex flex-1 flex-col items-center justify-center gap-8 px-8 py-8">
          <div className="flex size-32 items-center justify-center rounded-full border border-border bg-surface shadow-md">
            <span className="text-xl font-semibold text-muted">AF</span>
          </div>

          <div className="flex flex-col items-center gap-3 text-center">
            <MicLevelBars level={volumeLevel} active={busy || inCall} muted={muted} />
            <div className="space-y-1">
              <p className="text-lg font-medium">{voicePhaseLabel(phase)}</p>
              <p className="text-sm text-muted">
                {inCall ? formatDuration(durationMs) : muted ? "已静音" : voiceVolumeLabel(volumeLevel)}
              </p>
            </div>
            {error && (
              <p className="max-w-sm text-sm text-danger">
                {error}
              </p>
            )}
            {diagnostic && phase === "error" && (
              <p className="max-w-sm text-xs text-muted">
                诊断：{voiceFailureStageLabel(diagnostic.stage)} · {diagnostic.message}
              </p>
            )}
          </div>
        </section>

        <footer className="flex items-center justify-center gap-8 border-t border-border px-6 py-5">
          {!canDial && !awaitingConsent && (
            <MicActivityFrame active={canToggleMute} level={volumeLevel} muted={muted}>
              <TooltipButton
                icon={muted ? <MicOff /> : <Mic />}
                tooltip={muted ? "取消静音" : canToggleMute ? voiceVolumeLabel(volumeLevel) : "接通后可静音"}
                tooltipSide="top"
                size="icon-lg"
                variant={muted ? "outline" : "secondary"}
                className={`relative rounded-full ${micWaveActive ? "text-success" : ""}`}
                disabled={!canToggleMute}
                onClick={handleToggleMute}
              />
            </MicActivityFrame>
          )}
          {awaitingConsent ? (
            <Button size="icon-lg" className="rounded-full" disabled aria-label="等待确认">
              <Phone />
            </Button>
          ) : canDial ? (
            <Button
              size="icon-lg"
              className="rounded-full"
              onClick={() => void requestStart()}
              aria-label="拨号"
            >
              <Phone />
            </Button>
          ) : (
            <Button
              variant="destructive"
              size="icon-lg"
              className="rounded-full"
              onClick={handleHangUp}
              aria-label="挂断"
            >
              <PhoneOff />
            </Button>
          )}
        </footer>
        <VoiceTunnelConsentDialog
          open={awaitingConsent}
          onConfirm={() => void requestConfirmTunnel()}
          onCancel={() => void requestCancelTunnel()}
        />
      </main>
    </TooltipProvider>
  );
}
