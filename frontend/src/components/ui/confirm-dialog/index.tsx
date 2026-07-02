/**
 * 非 shadcn，自写：项目通用确认弹窗。
 *
 * 原因：需要统一业务 double-check、按钮布局与可选倒计时；shadcn 没有对应成品组件。
 */
import { useEffect, useState, type ReactNode } from "react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui";
import { cn } from "@/utils/cn";

interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "default" | "destructive";
  layout?: "inline" | "stacked";
  align?: "left" | "center";
  confirmDelaySeconds?: number;
  loading?: boolean;
  disabled?: boolean;
  showCloseButton?: boolean;
  contentClassName?: string;
  overlayClassName?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel = "确认",
  cancelLabel = "取消",
  variant = "default",
  layout = "inline",
  align = "left",
  confirmDelaySeconds = 0,
  loading = false,
  disabled = false,
  showCloseButton = false,
  contentClassName,
  overlayClassName,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [remainingSeconds, setRemainingSeconds] = useState(0);

  useEffect(() => {
    if (!open || confirmDelaySeconds <= 0) {
      setRemainingSeconds(0);
      return;
    }

    setRemainingSeconds(confirmDelaySeconds);
    const timer = window.setInterval(() => {
      setRemainingSeconds((current) => Math.max(0, current - 1));
    }, 1000);

    return () => window.clearInterval(timer);
  }, [confirmDelaySeconds, open]);

  const confirmDisabled = disabled || loading || remainingSeconds > 0;
  const renderedConfirmLabel =
    remainingSeconds > 0 ? `${confirmLabel}（${remainingSeconds}）` : confirmLabel;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent
        data-hit
        showCloseButton={showCloseButton}
        overlayClassName={overlayClassName}
        className={contentClassName}
      >
        <div
          className={cn(
            "flex flex-col gap-2",
            align === "center" ? "text-center" : "text-left",
          )}
        >
          <DialogTitle>{title}</DialogTitle>
          {description ? (
            <DialogDescription>{description}</DialogDescription>
          ) : null}
        </div>
        {children}
        <DialogFooter
          className={cn(
            layout === "stacked" &&
              "flex-col-reverse sm:flex-col-reverse sm:justify-start [&_[data-slot=button]]:w-full",
          )}
        >
          <Button data-hit variant="outline" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            data-hit
            variant={variant === "destructive" ? "destructive" : "default"}
            onClick={onConfirm}
            loading={loading}
            disabled={confirmDisabled}
          >
            {renderedConfirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
