import { invoke } from "@tauri-apps/api/core";
import { MessageSquare, Mic, Phone, PhoneOff, ShieldCheck, ShieldX } from "lucide-react";

import { Button, TooltipButton, TooltipProvider } from "@/components/ui";
import { useVoiceStore } from "@/stores/voice";
import { voicePhaseLabel } from "@/stores/voiceStateMachine";
import { isTauri } from "@/utils/tauri";

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function volumeLabel(level: number): string {
  if (level > 66) return "音量清晰";
  if (level > 20) return "正在收音";
  return "等待声音";
}

export function VoiceCallApp() {
  const phase = useVoiceStore((s) => s.phase);
  const durationMs = useVoiceStore((s) => s.durationMs);
  const volumeLevel = useVoiceStore((s) => s.volumeLevel);
  const error = useVoiceStore((s) => s.error);
  const consentAccepted = useVoiceStore((s) => s.consentAccepted);
  const requestStart = useVoiceStore((s) => s.requestStartFromAnyWindow);
  const requestHangUp = useVoiceStore((s) => s.requestHangUp);
  const setTunnelConsentAccepted = useVoiceStore((s) => s.setTunnelConsentAccepted);

  const canDial = phase === "idle" || phase === "ended" || phase === "error";
  const inCall = phase === "active";
  const busy = phase === "dialing" || phase === "joining_room" || phase === "starting_agent";

  const openChat = () => {
    if (!isTauri()) return;
    void invoke("open_chat");
  };

  return (
    <TooltipProvider delayDuration={0}>
      <main className="flex h-screen flex-col bg-bg text-fg">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="text-sm font-medium text-muted">语音通话</span>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => void setTunnelConsentAccepted(!consentAccepted)}
          >
            {consentAccepted ? <ShieldCheck /> : <ShieldX />}
            {consentAccepted ? "已确认穿透" : "需确认穿透"}
          </Button>
        </header>

        <section className="flex flex-1 flex-col items-center justify-center gap-8 px-8 py-8">
          <div className="flex size-32 items-center justify-center rounded-full border border-border bg-surface shadow-md">
            <span className="text-xl font-semibold text-muted">AF</span>
          </div>

          <div className="flex flex-col items-center gap-3 text-center">
            <div className="flex items-end gap-1">
              {[0, 1, 2].map((i) => {
                const active = volumeLevel > i * 28 || busy || inCall;
                return (
                  <span
                    key={i}
                    className={`block w-3 rounded-full transition-all ${
                      active ? "h-8 bg-fg" : "h-5 bg-border"
                    }`}
                  />
                );
              })}
            </div>
            <div className="space-y-1">
              <p className="text-lg font-medium">{voicePhaseLabel(phase)}</p>
              <p className="text-sm text-muted">
                {inCall ? formatDuration(durationMs) : volumeLabel(volumeLevel)}
              </p>
            </div>
            {error && (
              <p className="max-w-sm text-sm text-danger">
                {error}
              </p>
            )}
          </div>
        </section>

        <footer className="flex items-center justify-center gap-8 border-t border-border px-6 py-5">
          {!canDial && (
            <>
              <TooltipButton
                icon={<MessageSquare />}
                tooltip="打开对话"
                tooltipSide="top"
                onClick={openChat}
              />
              <TooltipButton
                icon={<Mic />}
                tooltip={volumeLabel(volumeLevel)}
                tooltipSide="top"
                disabled
              />
            </>
          )}
          {canDial ? (
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
              onClick={() => void requestHangUp()}
              aria-label="挂断"
            >
              <PhoneOff />
            </Button>
          )}
        </footer>
      </main>
    </TooltipProvider>
  );
}
