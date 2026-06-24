import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
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
    <Dialog open={open} onOpenChange={(next) => {
      if (!next) onCancel();
    }}>
      <DialogContent showCloseButton={false} data-hit>
        <DialogHeader>
          <DialogTitle>确认语音通话前提</DialogTitle>
          <DialogDescription>
            语音通话需要你已经启动 voice_bridge，并让火山云通过公网穿透回调本机。
            确认后才会请求麦克风权限并开始拨号。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 text-sm text-muted">
          <p>本期不会自动启动 cloudflared，也不会保存火山凭证或公网 URL。</p>
          <p>如果公网穿透没有准备好，通话可能无法接通。</p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={onConfirm}>
            我已确认，继续拨号
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
