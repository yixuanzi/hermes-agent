/**
 * 待发送附件芯片：文件名 / 大小 / 上传状态 / 重试 / 删除。
 */
import { FileText, LoaderCircle, X } from 'lucide-react';

import { ChatAttachment } from '../../types';

export interface PendingAttachment {
  localId: string;
  file: File;
  previewUrl?: string;
  attachment?: ChatAttachment;
  status: 'uploading' | 'ready' | 'failed';
  error?: string;
}

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${size} B`;
}

interface AttachmentChipsProps {
  attachments: PendingAttachment[];
  onRemove: (localId: string) => void;
  onRetry: (localId: string) => void;
}

export function AttachmentChips({ attachments, onRemove, onRetry }: AttachmentChipsProps) {
  if (attachments.length === 0) {
    return null;
  }
  return (
    <div className="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] p-1.5">
      {attachments.map((attachment) => (
        <div
          key={attachment.localId}
          className={`flex max-w-[220px] items-center gap-1.5 rounded-[var(--aisoc-radius-sm)] border px-1.5 py-1 font-mono text-[10px] ${
            attachment.status === 'failed'
              ? 'border-[color-mix(in_srgb,var(--aisoc-danger)_40%,var(--aisoc-border))] text-[var(--aisoc-danger)]'
              : 'border-[var(--aisoc-border)] text-[var(--aisoc-muted)]'
          }`}
        >
          {attachment.previewUrl ? (
            <img src={attachment.previewUrl} alt="" className="h-5 w-5 shrink-0 rounded object-cover" />
          ) : (
            <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          )}
          <span className="max-w-[110px] truncate" title={attachment.file.name}>
            {attachment.file.name}
          </span>
          <span className="shrink-0 tabular-nums">{formatSize(attachment.file.size)}</span>
          {attachment.status === 'uploading' ? (
            <LoaderCircle className="h-3 w-3 shrink-0 animate-spin text-[var(--aisoc-accent)]" aria-label="Uploading" />
          ) : null}
          {attachment.status === 'failed' ? (
            <button
              type="button"
              onClick={() => onRetry(attachment.localId)}
              title={attachment.error || 'Retry upload'}
              className="shrink-0 font-bold hover:text-[var(--aisoc-text)]"
            >
              Retry
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => onRemove(attachment.localId)}
            aria-label={`Remove ${attachment.file.name}`}
            className="shrink-0 hover:text-[var(--aisoc-text)]"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
