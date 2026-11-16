/**
 * 消息流：按 kind 分发到各气泡组件；新消息自动滚底（用户上滚时不强制）；
 * 顶部展示 transportError / rejectedInput 提示；无会话时空态引导。
 */
import { useEffect, useRef } from 'react';
import { MessageSquare, X } from 'lucide-react';

import { useChatRuntime } from '../runtime/chatRuntime';
import { Message } from '../types';
import { ApprovalCard } from './bubbles/ApprovalCard';
import { AssistantBubble } from './bubbles/AssistantBubble';
import { ClarifyCard } from './bubbles/ClarifyCard';
import { DelegateBubble } from './bubbles/DelegateBubble';
import { ToolGroupBubble } from './bubbles/ToolGroupBubble';
import { UserBubble } from './bubbles/UserBubble';

const AUTO_SCROLL_THRESHOLD_PX = 96;

function renderMessage(message: Message, onOpenFile?: (path: string) => void) {
  const kind = message.kind || 'chat';
  if (kind === 'delegate-event') {
    return <DelegateBubble key={message.id} message={message} />;
  }
  if (kind === 'main-tools' || kind === 'delegate-tools') {
    return <ToolGroupBubble key={message.id} message={message} />;
  }
  if (message.sender === 'user') {
    return <UserBubble key={message.id} message={message} />;
  }
  return <AssistantBubble key={message.id} message={message} onOpenFile={onOpenFile} />;
}

interface MessageStreamProps {
  /** 点击 assistant 消息的 modifiedFiles 芯片（打开 drawer 文件预览） */
  onOpenFile?: (path: string) => void;
}

export function MessageStream({ onOpenFile }: MessageStreamProps) {
  const {
    activeConversation,
    transportError,
    rejectedInput,
    setTransportError,
    clearRejectedInput,
    createConversation,
  } = useChatRuntime();
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pinnedToBottomRef = useRef(true);

  const messageCount = activeConversation?.messages.length || 0;
  const lastMessage = activeConversation?.messages.at(-1);

  // 新消息 / 流式增量到达时，仅在用户仍贴近底部时自动滚底。
  useEffect(() => {
    if (pinnedToBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [activeConversation?.id, messageCount, lastMessage?.text]);

  // 切换会话时直接跳到底部。
  useEffect(() => {
    pinnedToBottomRef.current = true;
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [activeConversation?.id]);

  function handleScroll() {
    const container = scrollRef.current;
    if (!container) return;
    const distance = container.scrollHeight - container.scrollTop - container.clientHeight;
    pinnedToBottomRef.current = distance < AUTO_SCROLL_THRESHOLD_PX;
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {transportError ? (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-[color-mix(in_srgb,var(--aisoc-danger)_30%,var(--aisoc-border))] bg-[color-mix(in_srgb,var(--aisoc-danger)_8%,var(--aisoc-bg-alt))] px-4 py-2 text-xs text-[var(--aisoc-danger)]"
        >
          <span className="min-w-0 flex-1 break-words">{transportError}</span>
          <button
            type="button"
            onClick={() => setTransportError('')}
            aria-label="Dismiss error"
            className="shrink-0 rounded p-0.5 transition-colors hover:bg-[color-mix(in_srgb,var(--aisoc-danger)_15%,transparent)]"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      ) : null}
      {rejectedInput ? (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-[color-mix(in_srgb,var(--aisoc-warning)_30%,var(--aisoc-border))] bg-[color-mix(in_srgb,var(--aisoc-warning)_7%,var(--aisoc-bg-alt))] px-4 py-2 text-xs text-[var(--aisoc-warning)]"
        >
          <span className="min-w-0 flex-1 break-words">
            Message rejected, not sent: {rejectedInput.text.length > 80 ? `${rejectedInput.text.slice(0, 80)}…` : rejectedInput.text}
          </span>
          <button
            type="button"
            onClick={clearRejectedInput}
            aria-label="Dismiss rejection notice"
            className="shrink-0 rounded p-0.5 transition-colors hover:bg-[color-mix(in_srgb,var(--aisoc-warning)_15%,transparent)]"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-y-auto py-2"
        aria-label="Message list"
      >
        {!activeConversation || activeConversation.messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <MessageSquare className="h-8 w-8 text-[var(--aisoc-border-strong)]" aria-hidden="true" />
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.22em] text-[var(--aisoc-muted)]">
              Awaiting First Turn
            </div>
            <p className="max-w-xs text-xs leading-relaxed text-[var(--aisoc-muted)]">
              Type a message below to start a conversation, or enter <code className="font-mono text-[var(--aisoc-accent)]">@</code> to open Quick Commands.
            </p>
            {!activeConversation ? (
              <button
                type="button"
                onClick={createConversation}
                className="rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] px-3 py-1.5 font-mono text-[11px] font-bold text-[var(--aisoc-accent)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:bg-[var(--aisoc-accent-soft)]"
              >
                New Session
              </button>
            ) : null}
          </div>
        ) : (
          <>
            {activeConversation.messages.map((message) => renderMessage(message, onOpenFile))}
            {activeConversation.pendingApproval ? <ApprovalCard conversation={activeConversation} /> : null}
            {activeConversation.pendingClarify ? <ClarifyCard conversation={activeConversation} /> : null}
          </>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
