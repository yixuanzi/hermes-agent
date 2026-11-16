/**
 * 用户消息气泡：右对齐、accent 边框，附带附件摘要芯片。
 */
import { FileText, Image as ImageIcon } from 'lucide-react';

import { useCurrentUser } from '../../../lib/authContext';
import { Message } from '../../types';
import { initialsAvatar } from '../../lib/identity';

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${size} B`;
}

export function UserBubble({ message }: { message: Message }) {
  const currentUser = useCurrentUser();
  const displayName = currentUser?.display_name || currentUser?.username || 'You';
  const avatar = initialsAvatar(currentUser?.username || displayName);

  return (
    <div className="flex justify-end px-4 py-1.5">
      <div className="max-w-[78%] min-w-0">
        <div className="mb-1 flex items-center justify-end gap-1.5">
          <span className="font-mono text-[9px] font-bold uppercase tracking-[0.18em] text-[var(--aisoc-muted)]">
            {displayName}
          </span>
          <span
            aria-hidden="true"
            className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[8px] font-bold text-white"
            style={{ backgroundColor: avatar.color }}
          >
            {avatar.initials}
          </span>
        </div>
        <div className="whitespace-pre-wrap break-words rounded-[var(--aisoc-radius-md)] border border-[color-mix(in_srgb,var(--aisoc-accent)_35%,var(--aisoc-border))] bg-[var(--aisoc-accent-soft)] px-3 py-2 text-[13px] leading-relaxed text-[var(--aisoc-text)]">
          {message.text}
        </div>
        {message.attachments?.length ? (
          <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
            {message.attachments.map((attachment) => (
              <span
                key={attachment.id}
                title={attachment.display_name}
                className="flex max-w-[220px] items-center gap-1 rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--aisoc-muted)]"
              >
                {attachment.kind === 'image' ? (
                  <ImageIcon className="h-3 w-3 shrink-0" aria-hidden="true" />
                ) : (
                  <FileText className="h-3 w-3 shrink-0" aria-hidden="true" />
                )}
                <span className="truncate">{attachment.display_name}</span>
                <span className="shrink-0 tabular-nums">{formatSize(attachment.size)}</span>
              </span>
            ))}
          </div>
        ) : null}
        <div className="mt-1 text-right font-mono text-[9px] tabular-nums text-[var(--aisoc-muted)]">
          {message.timestamp}
        </div>
      </div>
    </div>
  );
}
