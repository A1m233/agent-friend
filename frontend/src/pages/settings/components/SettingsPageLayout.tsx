import type { ReactNode } from "react";

interface SettingsPageLayoutProps {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function SettingsPageLayout({
  title,
  actions,
  children,
}: SettingsPageLayoutProps) {
  return (
    <div className="h-screen overflow-y-auto">
      <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
        <div className="flex items-center justify-between gap-3 h-7">
          <h2 className="text-xl font-semibold">{title}</h2>
          {actions ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </div>
        {children}
      </div>
    </div>
  );
}
