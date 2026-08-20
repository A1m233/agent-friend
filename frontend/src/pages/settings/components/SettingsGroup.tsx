import type { ReactNode } from "react";

interface SettingsGroupProps {
  id?: string;
  title: string;
  description?: ReactNode;
  children: ReactNode;
}

export function SettingsGroup({ id, title, description, children }: SettingsGroupProps) {
  return (
    <section
      id={id}
      className="flex flex-col gap-4 rounded-lg border border-border bg-surface/50 p-5"
    >
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium text-muted">{title}</h3>
        {description ? (
          <div className="text-xs leading-relaxed text-muted">{description}</div>
        ) : null}
      </div>
      {children}
    </section>
  );
}
