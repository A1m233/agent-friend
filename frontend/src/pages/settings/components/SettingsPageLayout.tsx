import type { ReactNode } from "react";

import { PageHeader } from "@/components/app/PageHeader";
import { ScrollArea } from "@/components/ui";

interface SettingsPageLayoutProps {
  title: string;
  actions?: ReactNode;
  embedded?: boolean;
  children: ReactNode;
}

export function SettingsPageLayout({
  title,
  actions,
  embedded,
  children,
}: SettingsPageLayoutProps) {
  if (embedded) return <div className="grid gap-4">{children}</div>;
  return (
    <ScrollArea className="h-screen min-h-0" viewportClassName="vertical-page-viewport">
      <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
        <PageHeader title={title} actions={actions} />
        {children}
      </div>
    </ScrollArea>
  );
}
