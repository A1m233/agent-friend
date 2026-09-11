import { CustomReasoningInput } from "@/components/model-config/ReasoningSelection";
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
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  TextEllipsis,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui";
import { openSettings } from "@/lib/settings/navigation";
import {
  credentialGroupForModel,
  defaultModelRuntimeConfig,
  modelRuntimeConfigLabel,
  type ModelRuntimeConfig,
  useModelCatalogStore,
} from "@/lib/model-config";
import { useConversationStore } from "@/stores/conversation";
import { cn } from "@/utils/cn";

interface ConversationModelSelectorProps {
  selectedModel: string;
  selectedReasoningPreset?: string;
  disabled?: boolean;
  disabledReason?: string;
}

export function ConversationModelSelector({
  selectedModel,
  selectedReasoningPreset,
  disabled = false,
  disabledReason,
}: ConversationModelSelectorProps) {
  const catalog = useModelCatalogStore((state) => state.catalog);
  const modelSwitching = useConversationStore((state) => state.modelSwitching);
  const modelSwitchTarget = useConversationStore((state) => state.modelSwitchTarget);
  const currentReasoningPreset = useConversationStore(
    (state) => state.currentReasoningPreset,
  );
  const historyLoading = useConversationStore((state) => state.historyLoading);
  const selectRuntimeConfig = useConversationStore((state) => state.selectRuntimeConfig);
  const [announcement, setAnnouncement] = useState("");
  const [open, setOpen] = useState(false);
  const [openSubmenu, setOpenSubmenu] = useState<"model" | "reasoning" | null>(null);

  const selectedOption = catalog?.providers
    .flatMap((provider) => provider.models)
    .find((model) => model.id === selectedModel);
  const selectedLabel = selectedOption?.label ?? selectedModel;
  const selectedReasoning = selectedOption?.reasoning ?? {
    control: "unsupported" as const,
    defaultPreset: "none",
    presets: [{ id: "none", label: "不支持" }],
    allowCustom: false,
  };
  const selectedConfig: ModelRuntimeConfig = {
    model: selectedModel,
    reasoningPreset: selectedReasoningPreset ?? currentReasoningPreset
      ?? selectedReasoning.defaultPreset
      ?? "none",
  };
  const selectedPreset = selectedReasoning.presets.find(
    (preset) => preset.id === selectedConfig.reasoningPreset,
  );
  const reasoningLabel = selectedPreset?.label ?? (selectedReasoning.allowCustom && selectedConfig.reasoningPreset.startsWith("effort:") ? selectedConfig.reasoningPreset.slice(7) : selectedConfig.reasoningPreset);
  const unavailable = disabled || !catalog || modelSwitching || historyLoading;
  const reason = modelSwitching
    ? `正在切换到 ${modelSwitchTarget ?? "目标模型"}`
    : disabled
      ? disabledReason ?? "暂时不可用"
      : historyLoading
          ? "正在加载会话历史"
          : !catalog
            ? "模型列表尚未就绪"
            : undefined;

  const handleSelect = async (config: ModelRuntimeConfig) => {
    if (
      (config.model === selectedConfig.model
        && config.reasoningPreset === selectedConfig.reasoningPreset)
      || modelSwitching
    ) return;
    const result = await selectRuntimeConfig(config);
    if (result.status === "stale") return;
    if (result.status === "applied") {
      const message = `下一条回复将使用 ${modelRuntimeConfigLabel({
        model: result.model,
        reasoningPreset: result.reasoningPreset,
      })}`;
      setAnnouncement(message);
      toast.success(message);
      return;
    }
    const credentialGroup = credentialGroupForModel(config.model);
    toast.error("模型没有切换", {
      description: result.error.friendly,
      action: result.error.code === "credential_missing"
        ? {
            label: "模型与凭据",
            onClick: () =>
              void openSettings({
                target: "runtime",
                runtime: credentialGroup ? { groupId: credentialGroup } : undefined,
              }),
          }
        : undefined,
    });
  };

  return (
    <>
      <DropdownMenu
        modal={false}
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) {
            setOpenSubmenu(null);
            setAnnouncement("");
          }
        }}
      >
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
                  aria-label={reason
                    ? `当前模型：${selectedLabel}，推理强度：${reasoningLabel}。${reason}`
                    : `当前模型：${selectedLabel}，推理强度：${reasoningLabel}`}
                  className="max-w-52 gap-1 px-2 text-muted hover:text-fg"
                >
                  <TextEllipsis className="min-w-0 leading-snug" showTooltip={false}>
                    {`${selectedLabel} · ${reasoningLabel}`}
                  </TextEllipsis>
                  {!modelSwitching && <ChevronDown className="size-4" aria-hidden="true" />}
                </Button>
              </DropdownMenuTrigger>
            </span>
          </TooltipTrigger>
          <TooltipContent side="top">
            {reason ?? "选择模型"}
          </TooltipContent>
        </Tooltip>
        <DropdownMenuContent align="start" side="top" className="min-w-64">
          <DropdownMenuSub
            open={openSubmenu === "model"}
            onOpenChange={(nextOpen) => {
              if (nextOpen) setOpenSubmenu("model");
            }}
          >
            <DropdownMenuSubTrigger
              className="group"
              onFocus={() => setOpenSubmenu("model")}
            >
              <span>模型</span>
              <span className="ml-auto max-w-36 truncate text-muted group-data-[state=open]:text-accent-fg">
                {selectedLabel}
              </span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent
              sideOffset={8}
              className="min-w-80"
              onEscapeKeyDown={(event) => {
                event.preventDefault();
                setOpen(false);
                setOpenSubmenu(null);
              }}
            >
              {catalog?.providers.map((provider, providerIndex) => (
                <DropdownMenuGroup key={provider.id}>
                  {providerIndex > 0 && <DropdownMenuSeparator />}
                  <DropdownMenuLabel className="text-muted">{provider.label}</DropdownMenuLabel>
                  {provider.models.map((model) => (
                    <DropdownMenuItem
                      key={model.id}
                      className="group"
                      onSelect={(event) => {
                        event.preventDefault();
                        void handleSelect(defaultModelRuntimeConfig(model.id));
                      }}
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
            </DropdownMenuSubContent>
          </DropdownMenuSub>

          <DropdownMenuSub
            open={openSubmenu === "reasoning"}
            onOpenChange={(nextOpen) => {
              if (nextOpen) setOpenSubmenu("reasoning");
            }}
          >
            <DropdownMenuSubTrigger
              className="group"
              onFocus={() => setOpenSubmenu("reasoning")}
            >
              <span>推理强度</span>
              <span className="ml-auto text-muted group-data-[state=open]:text-accent-fg">
                {reasoningLabel}
              </span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent
              sideOffset={8}
              className="min-w-48"
              onEscapeKeyDown={(event) => {
                event.preventDefault();
                setOpen(false);
                setOpenSubmenu(null);
              }}
            >
              {selectedReasoning.control === "fixed"
                || selectedReasoning.control === "unsupported" ? (
                  <DropdownMenuLabel className="font-normal text-muted">
                    {reasoningLabel}
                  </DropdownMenuLabel>
                ) : (
                  <DropdownMenuRadioGroup value={selectedConfig.reasoningPreset}>
                    {selectedReasoning.presets.map((preset) => (
                      <DropdownMenuRadioItem
                        key={preset.id}
                        value={preset.id}
                        disabled={modelSwitching}
                        onSelect={(event) => {
                          event.preventDefault();
                          void handleSelect({
                            model: selectedModel,
                            reasoningPreset: preset.id,
                          });
                        }}
                      >
                        {preset.label}
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                )}
              {selectedReasoning.allowCustom && <CustomReasoningInput disabled={modelSwitching} onChange={(reasoningPreset) => void handleSelect({ model: selectedModel, reasoningPreset })} />}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
      <span className="sr-only" aria-live="polite">{announcement}</span>
    </>
  );
}
