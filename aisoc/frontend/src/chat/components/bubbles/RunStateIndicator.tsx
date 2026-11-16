/**
 * lastKnownRunState 状态条：running 转圈 / waiting_for_* / interrupted（可恢复）/
 * error；idle 或未知时不渲染。delegate 前台时显示 foregroundAgentName。
 */
import { CircleAlert, LoaderCircle, OctagonPause, Play, ShieldAlert } from 'lucide-react';

import { useChatRuntime } from '../../runtime/chatRuntime';
import { Conversation } from '../../types';

export function RunStateIndicator({ conversation }: { conversation: Conversation }) {
  const { resumeActiveConversation } = useChatRuntime();
  const state = conversation.lastKnownRunState;
  if (!state || state === 'idle') {
    return null;
  }
  const actor =
    conversation.foregroundSource === 'delegate' && conversation.foregroundAgentName
      ? conversation.foregroundAgentName
      : 'AISOC';

  let icon: React.ReactNode = null;
  let label = '';
  let tone = 'text-[var(--aisoc-muted)]';
  if (state === 'running' || state === 'waiting_for_delegate_input') {
    icon = <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />;
    label = state === 'running' ? `${actor} · Running` : `${actor} · Waiting for delegate input`;
    tone = 'text-[var(--aisoc-accent)]';
  } else if (state === 'waiting_for_approval') {
    icon = <ShieldAlert className="h-3 w-3" aria-hidden="true" />;
    label = `${actor} · Waiting for approval`;
    tone = 'text-[var(--aisoc-warning)]';
  } else if (state === 'waiting_for_clarify') {
    icon = <ShieldAlert className="h-3 w-3" aria-hidden="true" />;
    label = `${actor} · Waiting for clarification`;
    tone = 'text-[var(--aisoc-warning)]';
  } else if (state === 'interrupted') {
    icon = <OctagonPause className="h-3 w-3" aria-hidden="true" />;
    label = `${actor} · Interrupted`;
    tone = 'text-[var(--aisoc-warning)]';
  } else if (state === 'error') {
    icon = <CircleAlert className="h-3 w-3" aria-hidden="true" />;
    label = `${actor} · Session error`;
    tone = 'text-[var(--aisoc-danger)]';
  } else {
    label = `${actor} · ${state.replace(/_/g, ' ')}`;
  }

  return (
    <div
      role="status"
      className={`flex shrink-0 items-center gap-2 border-t border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] px-4 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] ${tone}`}
    >
      {icon}
      <span>{label}</span>
      {state === 'interrupted' ? (
        <button
          type="button"
          onClick={resumeActiveConversation}
          className="ml-auto flex items-center gap-1 rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] px-2 py-0.5 font-mono text-[10px] font-bold text-[var(--aisoc-accent)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:bg-[var(--aisoc-accent-soft)]"
        >
          <Play className="h-2.5 w-2.5" aria-hidden="true" />
          Resume
        </button>
      ) : null}
    </div>
  );
}
