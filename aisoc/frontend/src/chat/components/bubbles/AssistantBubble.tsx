/**
 * assistant 聊天气泡：react-markdown 渲染 + pending 光标 + srcagent 来源徽标 +
 * modifiedFiles 文件芯片（点击交给上层打开 drawer 预览）。
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { FileText } from 'lucide-react';

import { BrandMark } from '../../../components/BrandMark';
import { Message } from '../../types';
import { ASSISTANT_NAME } from '../../lib/identity';

/** 紧凑暗色 markdown 渲染（也被 drawer 文件预览复用） */
export function ChatMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h1 className="mb-2 mt-3 text-base font-bold text-[var(--aisoc-text)] first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-1.5 mt-3 text-sm font-bold text-[var(--aisoc-text)] first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1 mt-2.5 text-[13px] font-semibold text-[var(--aisoc-text)] first:mt-0">{children}</h3>,
        p: ({ children }) => <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="my-1.5 list-disc space-y-0.5 pl-5">{children}</ul>,
        ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5">{children}</ol>,
        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
        blockquote: ({ children }) => (
          <blockquote className="my-2 border-l-2 border-[var(--aisoc-border-strong)] pl-3 text-[var(--aisoc-muted)]">{children}</blockquote>
        ),
        pre: ({ children }) => (
          <pre className="my-2 overflow-x-auto rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg)] p-2.5 text-xs leading-relaxed">
            {children}
          </pre>
        ),
        code: ({ children }) => (
          <code className="rounded bg-[color-mix(in_srgb,var(--aisoc-border)_45%,transparent)] px-1 py-0.5 font-mono text-[11px] text-[var(--aisoc-accent-strong)]">
            {children}
          </code>
        ),
        table: ({ children }) => (
          <div className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-xs">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-[var(--aisoc-border)] bg-[var(--aisoc-panel-strong)] px-2 py-1 text-left font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]">
            {children}
          </th>
        ),
        td: ({ children }) => <td className="border border-[var(--aisoc-border)] px-2 py-1">{children}</td>,
        a: ({ href, children }) => {
          const external = Boolean(href && /^https?:\/\//i.test(href));
          return (
            <a
              href={href}
              target={external ? '_blank' : undefined}
              rel={external ? 'noreferrer' : undefined}
              className="text-[var(--aisoc-accent)] underline decoration-[color-mix(in_srgb,var(--aisoc-accent)_45%,transparent)] underline-offset-2 hover:text-[var(--aisoc-accent-strong)]"
            >
              {children}
            </a>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

interface AssistantBubbleProps {
  message: Message;
  /** 点击 modifiedFiles 文件芯片时回调（由页面接 drawer） */
  onOpenFile?: (path: string) => void;
}

export function AssistantBubble({ message, onOpenFile }: AssistantBubbleProps) {
  return (
    <div className="flex justify-start px-4 py-1.5">
      <div className="max-w-[78%] min-w-0">
        {message.srcagent ? (
          <div className="mb-1 font-mono text-[9px] font-bold uppercase tracking-[0.18em] text-[var(--aisoc-danger)]">
            {message.srcagent} · delegate
          </div>
        ) : (
          <div className="mb-1 flex items-center gap-1.5">
            <BrandMark size={14} />
            <span className="font-mono text-[9px] font-bold uppercase tracking-[0.18em] text-[var(--aisoc-muted)]">
              {ASSISTANT_NAME}
            </span>
          </div>
        )}
        <div className="rounded-[var(--aisoc-radius-md)] border border-[var(--aisoc-border)] bg-[var(--aisoc-panel)] px-3 py-2 text-[13px] text-[var(--aisoc-text)]">
          <ChatMarkdown content={message.text} />
          {message.pending ? (
            <span
              aria-label="Generating"
              className="ml-0.5 inline-block h-3.5 w-[7px] translate-y-[2px] animate-pulse rounded-[1px] bg-[var(--aisoc-accent)]"
            />
          ) : null}
        </div>
        {message.modifiedFiles?.length ? (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {message.modifiedFiles.map((path) => (
              <button
                key={path}
                type="button"
                onClick={() => onOpenFile?.(path)}
                title={path}
                className="flex max-w-[240px] items-center gap-1 rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--aisoc-muted)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:text-[var(--aisoc-accent)]"
              >
                <FileText className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="truncate">{path.split('/').filter(Boolean).pop() || path}</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="mt-1 font-mono text-[9px] tabular-nums text-[var(--aisoc-muted)]">
          {message.timestamp}
        </div>
      </div>
    </div>
  );
}
