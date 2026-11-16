import type { ReactNode } from "react";

type StateBlockKind = "loading" | "empty" | "error" | "success";

type StateBlockProps = {
  kind: StateBlockKind;
  title: string;
  message?: string;
  actions?: ReactNode;
};

/** 语义边框色：状态信息同时由标题文字承载（非颜色标识），边框仅作辅助。 */
const KIND_BORDER: Record<StateBlockKind, string> = {
  loading: "border-[rgba(34,211,238,0.4)]",
  empty: "border-[rgba(148,163,184,0.42)]",
  error: "border-[rgba(253,164,175,0.6)]",
  success: "border-[rgba(110,231,183,0.54)]",
};

export function StateBlock({ kind, title, message, actions }: StateBlockProps) {
  return (
    <section
      className={`state-block state-block-${kind} rounded-[var(--aisoc-radius-md)] border bg-aisoc-panel p-[calc(14px*var(--density-scale))] shadow-[var(--aisoc-shadow)] ${KIND_BORDER[kind]}`}
    >
      <h3 className="mb-1.5 mt-0">{title}</h3>
      {message ? <p className="subtle-copy !m-0">{message}</p> : null}
      {actions ? <div className="state-block-actions mt-[calc(10px*var(--density-scale))] flex flex-wrap gap-[calc(8px*var(--density-scale))]">{actions}</div> : null}
    </section>
  );
}
