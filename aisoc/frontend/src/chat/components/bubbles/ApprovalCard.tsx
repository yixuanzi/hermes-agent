/**
 * 审批请求卡片：命令代码块 + 描述 + once/session/always/deny 四个按钮。
 */
import { ShieldAlert } from 'lucide-react';

import { useChatRuntime } from '../../runtime/chatRuntime';
import { Conversation } from '../../types';

type ApprovalChoice = 'once' | 'session' | 'always' | 'deny';

const CHOICE_LABELS: Record<ApprovalChoice, string> = {
  once: 'Once',
  session: 'This session',
  always: 'Always allow',
  deny: 'Reject',
};

export function ApprovalCard({ conversation }: { conversation: Conversation }) {
  const { respondApproval } = useChatRuntime();
  const approval = conversation.pendingApproval;
  if (!approval) {
    return null;
  }
  const choices = (approval.choices.length
    ? approval.choices
    : ['once', 'session', 'always', 'deny']) as ApprovalChoice[];

  return (
    <section
      aria-label="Approval request"
      className="mx-4 my-2 rounded-[var(--aisoc-radius-md)] border border-[color-mix(in_srgb,var(--aisoc-warning)_30%,var(--aisoc-border))] bg-[color-mix(in_srgb,var(--aisoc-warning)_6%,var(--aisoc-panel))] p-3"
    >
      <div className="flex items-start gap-2.5">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--aisoc-warning)]" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-[var(--aisoc-warning)]">
            Approval required
          </div>
          {approval.command ? (
            <pre className="mt-2 overflow-x-auto rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg)] p-2 font-mono text-[11px] leading-relaxed text-[var(--aisoc-text)]">
              <code>{approval.command}</code>
            </pre>
          ) : null}
          {approval.description ? (
            <p className="mt-2 text-xs leading-relaxed text-[var(--aisoc-muted)]">{approval.description}</p>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            {choices.map((choice) => (
              <button
                key={choice}
                type="button"
                onClick={() => respondApproval(choice)}
                className={`rounded-[var(--aisoc-radius-sm)] border px-3 py-1 font-mono text-[11px] font-bold transition-colors ${
                  choice === 'deny'
                    ? 'border-[color-mix(in_srgb,var(--aisoc-danger)_35%,var(--aisoc-border))] text-[var(--aisoc-danger)] hover:bg-[color-mix(in_srgb,var(--aisoc-danger)_10%,transparent)]'
                    : 'border-[var(--aisoc-border)] text-[var(--aisoc-text)] hover:border-[var(--aisoc-border-strong)] hover:bg-[var(--aisoc-panel-strong)]'
                }`}
              >
                {CHOICE_LABELS[choice] || choice}
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
