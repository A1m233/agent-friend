import { Archive, Pencil, type LucideIcon } from "lucide-react";
import {
  ContextMenuItem,
  DropdownMenuItem,
} from "@/components/ui";

export interface SessionActionContext {
  sessionId: string;
  title: string;
  hasActiveRun: boolean;
  hasDraft: boolean;
}

interface SessionActionHandlers {
  rename(context: SessionActionContext): void;
  archive(context: SessionActionContext): void;
}

export interface SessionMenuAction {
  id: "rename" | "archive";
  label: string;
  disabled: boolean;
  disabledReason?: string;
  icon: LucideIcon;
  run(): void;
}

export function createSessionMenuActions(
  context: SessionActionContext,
  handlers: SessionActionHandlers,
): SessionMenuAction[] {
  const archiveDisabledReason = context.hasActiveRun
    ? "运行中，暂不能归档"
    : context.hasDraft
      ? "有未发送草稿，暂不能归档"
      : undefined;

  return [
    {
      id: "rename",
      label: "重命名",
      disabled: false,
      icon: Pencil,
      run: () => handlers.rename(context),
    },
    {
      id: "archive",
      label: archiveDisabledReason ?? "归档",
      disabled: archiveDisabledReason !== undefined,
      disabledReason: archiveDisabledReason,
      icon: Archive,
      run: () => handlers.archive(context),
    },
  ];
}

export function SessionDropdownMenuItems({ actions }: { actions: SessionMenuAction[] }) {
  return actions.map((action) => {
    const Icon = action.icon;
    return (
      <DropdownMenuItem key={action.id} disabled={action.disabled} onSelect={action.run}>
        <Icon />
        {action.label}
      </DropdownMenuItem>
    );
  });
}

export function SessionContextMenuItems({ actions }: { actions: SessionMenuAction[] }) {
  return actions.map((action) => {
    const Icon = action.icon;
    return (
      <ContextMenuItem key={action.id} disabled={action.disabled} onSelect={action.run}>
        <Icon />
        {action.label}
      </ContextMenuItem>
    );
  });
}
