/**
 * 澄清请求卡片：question + choices 按钮；无 choices 或点「自定义回答」时
 * markClarifyAwaitingText，之后由 composer 输入作为澄清回复发送。
 */
import { MessageCircleQuestion } from 'lucide-react';

import { useChatRuntime } from '../../runtime/chatRuntime';
import { Conversation } from '../../types';

export function ClarifyCard({ conversation }: { conversation: Conversation }) {
  const { respondClarify, markClarifyAwaitingText } = useChatRuntime();
  const clarify = conversation.pendingClarify;
  if (!clarify) {
    return null;
  }

  return (
    <section
      aria-label="Clarification request"
      className="mx-4 my-2 rounded-[var(--aisoc-radius-md)] border border-[color-mix(in_srgb,var(--aisoc-accent)_30%,var(--aisoc-border))] bg-[color-mix(in_srgb,var(--aisoc-accent)_5%,var(--aisoc-panel))] p-3"
    >
      <div className="flex items-start gap-2.5">
        <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-[var(--aisoc-accent)]" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-[var(--aisoc-accent)]">
            Clarification needed
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-[var(--aisoc-text)]">{clarify.question}</p>
          {clarify.awaitingText ? (
            <p className="mt-2 rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg)] px-2.5 py-1.5 text-[11px] text-[var(--aisoc-muted)]">
              Type your answer in the input box below; your next message will be sent as the clarification reply.
            </p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {clarify.choices.map((choice) => (
                <button
                  key={choice}
                  type="button"
                  onClick={() => respondClarify(choice)}
                  className="rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] px-3 py-1 font-mono text-[11px] font-bold text-[var(--aisoc-text)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:bg-[var(--aisoc-panel-strong)]"
                >
                  {choice}
                </button>
              ))}
              <button
                type="button"
                onClick={markClarifyAwaitingText}
                className="rounded-[var(--aisoc-radius-sm)] border border-[color-mix(in_srgb,var(--aisoc-accent)_45%,var(--aisoc-border))] bg-[var(--aisoc-accent-soft)] px-3 py-1 font-mono text-[11px] font-bold text-[var(--aisoc-accent)] transition-colors hover:bg-[color-mix(in_srgb,var(--aisoc-accent)_22%,transparent)]"
              >
                Custom answer
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
