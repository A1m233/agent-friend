import { useState } from "react";
import {
  Calculator,
  ChevronRight,
  FilePenLine,
  FileText,
  ListTodo,
  Search,
  Sparkles,
  SquareTerminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  TextEllipsis,
} from "@/components/ui";
import type { ToolBlock } from "@/types/chat";
import { cn } from "@/utils/cn";
import { ToolCard } from "./ToolCard";
import {
  toolActivitySummary,
  toolPresentation,
  type ToolActivityCategory,
} from "./toolActivityPresentation";

const CATEGORY_ICONS: Record<ToolActivityCategory, LucideIcon> = {
  search: Search,
  read: FileText,
  write: FilePenLine,
  command: SquareTerminal,
  skill: Sparkles,
  todo: ListTodo,
  query: Calculator,
  other: Wrench,
};

interface ToolDisclosureRowProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  summary: string;
  category: ToolActivityCategory;
  children: React.ReactNode;
  contentClassName?: string;
}

function ToolDisclosureRow({
  open,
  onOpenChange,
  summary,
  category,
  children,
  contentClassName,
}: ToolDisclosureRowProps) {
  const ActivityIcon = CATEGORY_ICONS[category];

  return (
    <Collapsible open={open} onOpenChange={onOpenChange}>
      <CollapsibleTrigger
        className={cn(
          "group inline-flex max-w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs text-muted",
          "transition-colors hover:bg-surface/60 hover:text-fg",
          "focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:outline-hidden",
        )}
      >
        <ActivityIcon
          data-slot="tool-activity-icon"
          aria-hidden="true"
          className="size-3.5 shrink-0"
        />
        <TextEllipsis className="min-w-0">{summary}</TextEllipsis>
        <ChevronRight
          data-slot="tool-activity-chevron"
          aria-hidden="true"
          className={cn(
            "size-3.5 shrink-0 opacity-0 transition-[opacity,transform]",
            "group-hover:opacity-100 group-focus-visible:opacity-100",
            "group-data-[state=open]:rotate-90",
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className={contentClassName}>{children}</CollapsibleContent>
    </Collapsible>
  );
}

function ToolActivityItem({
  block,
  open,
  onOpenChange,
}: {
  block: ToolBlock;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const presentation = toolPresentation(block);
  return (
    <ToolDisclosureRow
      open={open}
      onOpenChange={onOpenChange}
      summary={presentation.summary}
      category={presentation.category}
      contentClassName="ml-5 pt-1 pb-1"
    >
      <ToolCard block={block} embedded />
    </ToolDisclosureRow>
  );
}

export function ToolActivity({ blocks }: { blocks: ToolBlock[] }) {
  const [groupOpen, setGroupOpen] = useState(false);
  const [itemOpenById, setItemOpenById] = useState<Record<string, boolean>>({});
  if (blocks.length === 0) return null;

  const setItemOpen = (toolCallId: string, open: boolean) => {
    setItemOpenById((current) => ({ ...current, [toolCallId]: open }));
  };

  if (blocks.length === 1) {
    const block = blocks[0];
    const open = itemOpenById[block.toolCallId] ?? groupOpen;
    return (
      <div className="w-full">
        <ToolActivityItem
          block={block}
          open={open}
          onOpenChange={(nextOpen) => {
            setGroupOpen(nextOpen);
            setItemOpen(block.toolCallId, nextOpen);
          }}
        />
      </div>
    );
  }

  return (
    <div className="w-full">
      <ToolDisclosureRow
        open={groupOpen}
        onOpenChange={setGroupOpen}
        summary={toolActivitySummary(blocks)}
        category={toolPresentation(blocks[0]).category}
        contentClassName="ml-2 mt-1 space-y-0.5 border-l border-border pl-2"
      >
        {blocks.map((block) => (
          <ToolActivityItem
            key={block.toolCallId}
            block={block}
            open={itemOpenById[block.toolCallId] ?? false}
            onOpenChange={(open) => setItemOpen(block.toolCallId, open)}
          />
        ))}
      </ToolDisclosureRow>
    </div>
  );
}
