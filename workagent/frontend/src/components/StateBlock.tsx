import type { ReactNode } from "react";

type StateBlockKind = "loading" | "empty" | "error" | "success";

type StateBlockProps = {
  kind: StateBlockKind;
  title: string;
  message?: string;
  actions?: ReactNode;
};

export function StateBlock({ kind, title, message, actions }: StateBlockProps) {
  return (
    <section className={`state-block state-block-${kind}`}>
      <h3>{title}</h3>
      {message ? <p className="subtle-copy">{message}</p> : null}
      {actions ? <div className="state-block-actions">{actions}</div> : null}
    </section>
  );
}
