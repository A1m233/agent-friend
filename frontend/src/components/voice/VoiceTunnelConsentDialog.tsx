import { ConfirmDialog } from "@/components/ui";

interface VoiceTunnelConsentDialogProps {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function VoiceTunnelConsentDialog({
  open,
  onConfirm,
  onCancel,
}: VoiceTunnelConsentDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="确认语音通话前提"
      description="语音通话需要你已经启动 voice_bridge，并让火山云通过公网穿透回调本机。确认后才会请求麦克风权限并开始拨号。"
      confirmLabel="我已确认，继续拨号"
      cancelLabel="取消"
      confirmDelaySeconds={3}
      layout="stacked"
      align="center"
      showCloseButton={false}
      overlayClassName="bg-transparent"
      contentClassName="w-[min(420px,calc(100vw-2rem))] max-w-none rounded-2xl bg-bg/95 shadow-2xl"
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <div className="space-y-2 text-sm text-muted">
        <p>本期不会自动启动 cloudflared，也不会保存火山凭证或公网 URL。</p>
        <p>如果公网穿透没有准备好，通话可能无法接通。</p>
      </div>
    </ConfirmDialog>
  );
}
