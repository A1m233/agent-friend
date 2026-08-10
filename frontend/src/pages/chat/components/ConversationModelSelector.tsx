import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { toast } from "sonner";

import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  TextEllipsis,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui";
import { openSettings } from "@/lib/settings/navigation";
import { useModelCatalogStore } from "@/lib/model-config";
import { useConversationStore } from "@/stores/conversation";
import { cn } from "@/utils/cn";

interface ConversationModelSelectorProps {
  selectedModel: string;
  disabled?: boolean;
  disabledReason?: string;
}

export function ConversationModelSelector({
  selectedModel,
  disabled = false,
  disabledReason,
}: ConversationModelSelectorProps) {
  const catalog = useModelCatalogStore((state) => state.catalog);
  const modelSwitching = useConversationStore((state) => state.modelSwitching);
  const modelSwitchTarget = useConversationStore((state) => state.modelSwitchTarget);
  const streaming = useConversationStore((state) => state.streaming);
  const historyLoading = useConversationStore((state) => state.historyLoading);
  const selectModel = useConversationStore((state) => state.selectModel);
  const [announcement, setAnnouncement] = useState("");

  const selectedOption = catalog?.providers
    .flatMap((provider) => provider.models)
    .find((model) => model.id === selectedModel);
  const selectedLabel = selectedOption?.label ?? selectedModel;
  const unavailable = disabled || !catalog || modelSwitching || streaming || historyLoading;
  const reason = modelSwitching
    ? `正在切换到 ${modelSwitchTarget ?? "目标模型"}`
    : disabled
      ? disabledReason ?? "暂时不可用"
      : streaming
        ? "请等待当前回复完成"
        : historyLoading
          ? "正在加载会话历史"
          : !catalog
            ? "模型列表尚未就绪"
            : undefined;

  const handleSelect = async (modelId: string) => {
    if (modelId === selectedModel || modelSwitching) return;
    const result = await selectModel(modelId);
    if (result.status === "stale") return;
    if (result.status === "applied") {
      const label = catalog?.providers
        .flatMap((provider) => provider.models)
        .find((model) => model.id === result.model)?.label ?? result.model;
      const message = `下一条回复将使用 ${label}`;
      setAnnouncement(message);
      toast.success(message);
      return;
    }
    toast.error("模型没有切换", {
      description: result.error.friendly,
      action: result.error.code === "credential_missing"
        ? {
            label: "模型与凭据",
            onClick: () => void openSettings({ target: "runtime" }),
          }
        : undefined,
    });
  };

  return (
    <>
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={cn("inline-flex min-w-0", unavailable && "cursor-not-allowed")}>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="pill"
                  disabled={unavailable}
                  loading={modelSwitching}
                  aria-label={reason ? `当前模型：${selectedLabel}。${reason}` : `当前模型：${selectedLabel}`}
                  className="max-w-52 px-2 text-muted hover:text-fg"
                >
                  <TextEllipsis className="min-w-0" showTooltip={false}>
                    {selectedLabel}
                  </TextEllipsis>
                  {!modelSwitching && <ChevronDown className="size-3.5" aria-hidden="true" />}
                </Button>
              </DropdownMenuTrigger>
            </span>
          </TooltipTrigger>
          <TooltipContent side="top">
            {reason ?? "选择模型"}
          </TooltipContent>
        </Tooltip>
        <DropdownMenuContent align="start" side="top" className="min-w-64">
          {catalog?.providers.map((provider, providerIndex) => (
            <DropdownMenuGroup key={provider.id}>
              {providerIndex > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel className="text-muted">{provider.label}</DropdownMenuLabel>
              {provider.models.map((model) => (
                <DropdownMenuItem
                  key={model.id}
                  className="group"
                  onSelect={() => void handleSelect(model.id)}
                  disabled={modelSwitching}
                >
                  <span className="min-w-0 flex-1 truncate">{model.label}</span>
                  {model.id === selectedModel && (
                    <Check
                      className="size-4 text-accent group-focus:text-accent-fg"
                      aria-label="当前模型"
                    />
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <span className="sr-only" aria-live="polite">{announcement}</span>
    </>
  );
}
