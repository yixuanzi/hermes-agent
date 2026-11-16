import type { ReactNode } from "react";

type PageMissionHeaderProps = {
  title: string;
  subtitle?: string;
  status?: ReactNode;
  actions?: ReactNode;
};

export function PageMissionHeader({ title, subtitle, status, actions }: PageMissionHeaderProps) {
  const hasMeta = Boolean(status || actions);

  return (
    <header className="page-mission-header flex items-start justify-between gap-[calc(14px*var(--density-scale))] rounded-[var(--aisoc-radius-md)] border border-aisoc-border bg-aisoc-panel p-[calc(14px*var(--density-scale))] shadow-[var(--aisoc-shadow)] backdrop-blur-[10px]">
      <div className="page-mission-copy">
        <h2 className="!mb-1.5 !mt-0">{title}</h2>
        {subtitle ? <p className="subtle-copy !m-0">{subtitle}</p> : null}
      </div>
      {hasMeta ? (
        <div className="page-mission-meta flex flex-wrap items-center gap-[calc(10px*var(--density-scale))]">
          {status ? <div className="page-mission-status">{status}</div> : null}
          {actions ? <div className="page-mission-actions flex flex-wrap gap-[calc(8px*var(--density-scale))]">{actions}</div> : null}
        </div>
      ) : null}
    </header>
  );
}
