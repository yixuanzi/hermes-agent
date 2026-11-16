/**
 * workflow 选中节点的详情浮层（WorkflowPanel 子组件）。
 */
import { X } from 'lucide-react';

import { WorkflowGraphNode } from '../../types';

function formatTimestamp(timestamp?: number): string {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

interface WorkflowNodeDetailsProps {
  node: WorkflowGraphNode;
  onClose: () => void;
}

export function WorkflowNodeDetails({ node, onClose }: WorkflowNodeDetailsProps) {
  return (
    <section className="absolute bottom-3 right-3 z-30 w-[min(20rem,calc(100%-1.5rem))] select-text overflow-hidden rounded-[var(--aisoc-radius-md)] border border-[var(--aisoc-border)] bg-[var(--aisoc-panel-strong)] backdrop-blur">
      <div className="flex items-center justify-between border-b border-[var(--aisoc-border)] px-3 py-1.5">
        <span className="font-mono text-[9px] font-bold uppercase tracking-[0.18em] text-[var(--aisoc-accent)]">
          Node Details
        </span>
        <button type="button" aria-label="Close node details" onClick={onClose} className="text-[var(--aisoc-muted)] hover:text-[var(--aisoc-text)]">
          <X className="h-3 w-3" aria-hidden="true" />
        </button>
      </div>
      <div className="max-h-56 space-y-2.5 overflow-y-auto overscroll-contain p-3 text-[10px]">
        <div className="text-sm font-semibold text-[var(--aisoc-text)]">{node.label}</div>
        <div className="grid grid-cols-2 gap-2 font-mono text-[var(--aisoc-muted)]">
          <div>TYPE<div className="mt-0.5 uppercase text-[var(--aisoc-text)]">{node.kind}</div></div>
          <div>STATUS<div className="mt-0.5 uppercase text-[var(--aisoc-text)]">{node.status}</div></div>
          <div>SOURCE<div className="mt-0.5 uppercase text-[var(--aisoc-text)]">{node.source}</div></div>
          <div>TIME<div className="mt-0.5 text-[var(--aisoc-text)]">{formatTimestamp(node.timestamp)}</div></div>
        </div>
        {node.agent ? (
          <div className="font-mono text-[var(--aisoc-danger)]">AGENT · {node.agent}</div>
        ) : null}
        {node.detail ? (
          <div className="whitespace-pre-wrap break-words rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg)] p-2 leading-relaxed text-[var(--aisoc-text)]">
            {node.detail}
          </div>
        ) : null}
        {node.argsPreview ? (
          <div>
            <div className="mb-1 font-mono text-[var(--aisoc-muted)]">ARGUMENTS</div>
            <pre className="whitespace-pre-wrap break-words rounded-r border-l-2 border-[var(--aisoc-accent)] bg-[var(--aisoc-bg)] p-2 text-[var(--aisoc-text)]">{node.argsPreview}</pre>
          </div>
        ) : null}
        {node.resultPreview ? (
          <div>
            <div className="mb-1 font-mono text-[var(--aisoc-muted)]">RESULT</div>
            <pre className="whitespace-pre-wrap break-words rounded-r border-l-2 border-[var(--aisoc-success)] bg-[var(--aisoc-bg)] p-2 text-[var(--aisoc-text)]">{node.resultPreview}</pre>
          </div>
        ) : null}
        {node.finalMessage ? (
          <div>
            <div className="mb-1 font-mono font-semibold tracking-wider text-[var(--aisoc-success)]">FINAL MESSAGE</div>
            <div className="whitespace-pre-wrap break-words rounded-[var(--aisoc-radius-sm)] border border-[color-mix(in_srgb,var(--aisoc-success)_30%,var(--aisoc-border))] bg-[color-mix(in_srgb,var(--aisoc-success)_6%,transparent)] p-2 leading-relaxed text-[var(--aisoc-text)]">
              {node.finalMessage}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
