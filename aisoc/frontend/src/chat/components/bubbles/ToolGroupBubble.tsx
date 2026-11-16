/**
 * 工具调用折叠组：kind 'main-tools'（ChainStep[]）与 'delegate-tools'
 * （DelegateToolCall[]）共用。默认折叠为 "N tools" 摘要，点击展开逐条明细。
 */
import { useState } from 'react';
import { Check, ChevronDown, ChevronRight, LoaderCircle, Wrench, X } from 'lucide-react';

import { ChainStep, DelegateToolCall, Message } from '../../types';

const PREVIEW_LIMIT = 160;

function truncate(value: string): string {
  const normalized = value.trim();
  return normalized.length > PREVIEW_LIMIT ? `${normalized.slice(0, PREVIEW_LIMIT)}…` : normalized;
}

interface ToolRow {
  key: string;
  name: string;
  running: boolean;
  failed: boolean;
  detail: string;
}

function rowsFromChainSteps(steps: ChainStep[]): ToolRow[] {
  return steps.map((step, index) => ({
    key: step.id || `step-${index}`,
    name: step.agentName,
    running: step.status === 'Processing' || step.status === 'Pending',
    failed: step.status === 'Failed',
    detail: truncate(step.message),
  }));
}

function rowsFromDelegateTools(calls: DelegateToolCall[]): ToolRow[] {
  return calls.map((call) => ({
    key: call.id,
    name: call.toolName,
    running: call.status === 'running',
    failed: false,
    detail: truncate(call.resultPreview || call.argsPreview || ''),
  }));
}

export function ToolGroupBubble({ message }: { message: Message }) {
  const [expanded, setExpanded] = useState(false);
  const delegate = message.kind === 'delegate-tools';
  const rows = delegate
    ? rowsFromDelegateTools(message.delegateTools || [])
    : rowsFromChainSteps(message.chainSteps || []);
  if (rows.length === 0) {
    return null;
  }
  const runningCount = rows.filter((row) => row.running).length;

  return (
    <div className="px-4 py-1">
      <div className="max-w-[78%] overflow-hidden rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)]">
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--aisoc-panel-strong)]"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-[var(--aisoc-muted)]" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-[var(--aisoc-muted)]" aria-hidden="true" />
          )}
          <Wrench className="h-3 w-3 shrink-0 text-[var(--aisoc-info)]" aria-hidden="true" />
          <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-[var(--aisoc-muted)]">
            {delegate ? `${message.srcagent || 'Delegate'} · ` : ''}
            {rows.length} tools
          </span>
          {runningCount > 0 ? (
            <span className="flex items-center gap-1 font-mono text-[9px] text-[var(--aisoc-accent)]">
              <LoaderCircle className="h-2.5 w-2.5 animate-spin" aria-hidden="true" />
              {runningCount} running
            </span>
          ) : null}
          <span className="ml-auto font-mono text-[9px] tabular-nums text-[var(--aisoc-muted)]">
            {message.timestamp}
          </span>
        </button>
        {expanded ? (
          <ul className="border-t border-[var(--aisoc-border)]">
            {rows.map((row) => (
              <li
                key={row.key}
                className="flex items-start gap-2 border-b border-[color-mix(in_srgb,var(--aisoc-border)_55%,transparent)] px-2.5 py-1.5 last:border-b-0"
              >
                {row.running ? (
                  <LoaderCircle className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-[var(--aisoc-accent)]" aria-label="Running" />
                ) : row.failed ? (
                  <X className="mt-0.5 h-3 w-3 shrink-0 text-[var(--aisoc-danger)]" aria-label="Failed" />
                ) : (
                  <Check className="mt-0.5 h-3 w-3 shrink-0 text-[var(--aisoc-success)]" aria-label="Completed" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[10px] font-bold text-[var(--aisoc-text)]">{row.name}</div>
                  {row.detail ? (
                    <div className="mt-0.5 break-words font-mono text-[10px] leading-relaxed text-[var(--aisoc-muted)]">
                      {row.detail}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
