/**
 * kind 'delegate-event' 的系统提示行：居中小字（delegate 进入/退出前台）。
 */
import { ArrowLeftRight } from 'lucide-react';

import { Message } from '../../types';

export function DelegateBubble({ message }: { message: Message }) {
  return (
    <div className="flex items-center justify-center gap-2 px-4 py-2" role="status">
      <span className="h-px w-8 bg-[var(--aisoc-border)]" aria-hidden="true" />
      <ArrowLeftRight className="h-3 w-3 shrink-0 text-[var(--aisoc-muted)]" aria-hidden="true" />
      <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]">
        {message.text}
      </span>
      <span className="h-px w-8 bg-[var(--aisoc-border)]" aria-hidden="true" />
    </div>
  );
}
