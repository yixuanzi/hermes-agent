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
    <header className="page-mission-header">
      <div className="page-mission-copy">
        <h2>{title}</h2>
        {subtitle ? <p className="subtle-copy">{subtitle}</p> : null}
      </div>
      {hasMeta ? (
        <div className="page-mission-meta">
          {status ? <div className="page-mission-status">{status}</div> : null}
          {actions ? <div className="page-mission-actions">{actions}</div> : null}
        </div>
      ) : null}
    </header>
  );
}
