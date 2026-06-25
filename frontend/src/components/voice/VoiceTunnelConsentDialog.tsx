import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
  Button,
} from "@/components/ui";

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
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent
        data-hit
        showCloseButton={false}
        overlayClassName="bg-transparent"
        className="w-[min(420px,calc(100vw-2rem))] max-w-none rounded-2xl bg-bg/95 shadow-2xl"
      >
        <div className="flex flex-col gap-2 text-center sm:text-left">
          <DialogTitle>确认语音通话前提</DialogTitle>
          <DialogDescription>
            语音通话需要你已经启动 voice_bridge，并让火山云通过公网穿透回调本机。
            确认后才会请求麦克风权限并开始拨号。
          </DialogDescription>
        </div>
        <div className="space-y-2 text-sm text-muted">
          <p>本期不会自动启动 cloudflared，也不会保存火山凭证或公网 URL。</p>
          <p>如果公网穿透没有准备好，通话可能无法接通。</p>
        </div>
        <DialogFooter>
          <Button data-hit variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button data-hit onClick={onConfirm}>
            我已确认，继续拨号
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
