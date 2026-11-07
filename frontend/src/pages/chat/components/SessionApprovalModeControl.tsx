import { Check, ChevronDown, Hand, ShieldAlert, ShieldCheck } from "lucide-react";

import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui";
import { useConnectionStore, useConversationStore } from "@/stores";
import { cn } from "@/utils/cn";
import { useSessionApprovalModeActions } from "./sessionApprovalModeContext";

export function SessionApprovalModeControl() {
  const status = useConnectionStore((state) => state.status);
  const mode = useConversationStore((state) => state.approvalMode);
  const changing = useConversationStore((state) => state.approvalModeChanging);
  const {
    requestSessionAutoFromComposer,
    switchReviewMode,
  } = useSessionApprovalModeActions();
  const disabled = status !== "online" || changing;
  const disabledReason = status !== "online"
      ? "本机服务连接后可调整授权模式"
      : changing
        ? "正在切换授权模式"
        : undefined;
  const review = mode === "auto_review";
  const auto = mode === "session_auto";
  const ModeIcon = auto ? ShieldAlert : review ? ShieldCheck : Hand;

  return (
    <DropdownMenu modal={false}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("inline-flex", disabled && "cursor-not-allowed")}>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="pill"
                disabled={disabled}
                loading={changing}
                aria-label={auto
                  ? "当前授权模式：本会话无需授权，高风险"
                  : review ? "当前授权模式：帮我审批" : "当前授权模式：每次授权"}
                className={cn(
                  "gap-1 px-2",
                  auto
                    ? "text-warning hover:text-warning"
                    : "text-muted hover:text-fg",
                )}
              >
                <ModeIcon className="size-4" aria-hidden="true" />
                <span>{auto ? "本会话无需授权" : review ? "帮我审批" : "每次授权"}</span>
                {!changing ? <ChevronDown className="size-4" aria-hidden="true" /> : null}
              </Button>
            </DropdownMenuTrigger>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">{disabledReason ?? "选择本会话授权模式"}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="start" side="top" className="min-w-72">
        <DropdownMenuLabel>本会话授权模式</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="group items-start gap-2"
          onSelect={() => {
            if (mode !== "per_request") void switchReviewMode("per_request");
          }}
        >
          <Hand className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block font-medium">每次授权</span>
            <span className="block text-xs text-muted group-focus:text-accent-fg">
              每个文件或命令请求都由你决定
            </span>
          </span>
          {mode === "per_request" ? <Check className="mt-0.5 size-4" aria-label="当前模式" /> : null}
        </DropdownMenuItem>
        <DropdownMenuItem
          className="group items-start gap-2"
          onSelect={() => { if (!review) void switchReviewMode("auto_review"); }}
        >
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block font-medium">帮我审批</span>
            <span className="block text-xs text-muted group-focus:text-accent-fg">
              由 AF 审查本会话的每个权限请求
            </span>
          </span>
          {review ? <Check className="mt-0.5 size-4" aria-label="当前模式" /> : null}
        </DropdownMenuItem>
        <DropdownMenuItem
          className="group items-start gap-2 text-warning focus:text-warning"
          onSelect={() => {
            if (!auto) requestSessionAutoFromComposer();
          }}
        >
          <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block font-medium">本会话无需授权</span>
            <span className="block text-xs opacity-80 group-focus:text-warning">
              后续普通请求不再逐项询问
            </span>
          </span>
          {auto ? <Check className="mt-0.5 size-4" aria-label="当前模式" /> : null}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
