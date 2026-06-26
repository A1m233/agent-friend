import type { ReactNode } from "react";

interface SettingsGroupProps {
  title: string;
  children: ReactNode;
}

export function SettingsGroup({ title, children }: SettingsGroupProps) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-surface/50 p-5">
      <h3 className="text-sm font-medium text-muted">{title}</h3>
      {children}
    </section>
  );
}
