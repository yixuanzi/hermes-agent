/**
 * 右下角悬浮聊天入口（统一聊天运行时版）：
 * - 悬浮球：有需要关注的会话时显示 chatAttentionCount 徽标；
 * - 点击展开小型聊天面板（380×560），复用 MessageStream / Composer，
 *   操作的是与 /chat 页同一个活跃会话（同一 AisocChatProvider）；
 * - /chat 路由下不渲染（主视图已在）。
 */
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ExternalLink, Plus, X } from 'lucide-react';

import { Composer, MessageStream } from './components';
import { useChatRuntime } from './runtime/chatRuntime';

/** 悬浮入口的 AI 助手字形:通信气泡 + AI 星火 + 卫星信号点(白色描边,落在签名渐变球上)。 */
function AssistantGlyph() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
      {/* 通信气泡 */}
      <path
        d="M5 4h14a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-8.6L6 20.8V17H5a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3Z"
        fill="none"
        stroke="#ffffff"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      {/* AI 星火 */}
      <path
        d="M11.6 7.4 12.7 10.1 15.4 11.2 12.7 12.3 11.6 15 10.5 12.3 7.8 11.2 10.5 10.1Z"
        fill="#ffffff"
      />
      {/* 卫星信号点 */}
      <circle cx="16.8" cy="7.6" r="1" fill="#ffffff" opacity="0.9" />
    </svg>
  );
}

export function FloatingChatWidget() {
  const location = useLocation();
  const navigate = useNavigate();
  const { activeConversation, chatAttentionCount, createConversation } = useChatRuntime();
  const [open, setOpen] = useState(false);

  // /chat 主视图自带完整聊天界面，悬浮球不再出现。
  if (location.pathname.startsWith('/chat')) {
    return null;
  }

  if (!open) {
    return (
      <div className="fixed bottom-6 right-6 z-50">
        {/* 呼吸光晕:引导点击,守 reduced-motion */}
        <span
          aria-hidden="true"
          className="fab-chat-glow pointer-events-none absolute inset-0 rounded-[20px]"
        />
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open AISOC Assistant"
          title="Open AISOC Assistant"
          className="fab-chat relative grid h-14 w-14 place-items-center rounded-[18px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--aisoc-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--aisoc-bg)]"
        >
          {chatAttentionCount > 0 ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 rounded-[18px] bg-[var(--aisoc-accent)] opacity-30 motion-safe:animate-ping"
            />
          ) : null}
          <AssistantGlyph />
          {chatAttentionCount > 0 ? (
            <span
              aria-label={`${chatAttentionCount} sessions need attention`}
              className="absolute -right-1.5 -top-1.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full border border-[var(--aisoc-bg)] bg-[var(--aisoc-danger)] px-1 font-mono text-[10px] font-bold leading-none text-white shadow-[0_2px_8px_rgba(0,0,0,0.35)]"
            >
              {chatAttentionCount > 99 ? '99+' : chatAttentionCount}
            </span>
          ) : null}
        </button>
      </div>
    );
  }

  const title = activeConversation?.title || 'New Session';

  return (
    <div
      role="dialog"
      aria-label="Floating chat panel"
      className="chat-shell fixed bottom-6 right-6 z-50 flex h-[560px] max-h-[calc(100vh-3rem)] w-[380px] max-w-[calc(100vw-3rem)] flex-col overflow-hidden rounded-[var(--aisoc-radius-lg)] border border-[var(--aisoc-border)] bg-[var(--aisoc-bg)] text-[var(--aisoc-text)] shadow-[0_18px_48px_rgba(6,8,12,0.55)]"
    >
      <header className="flex shrink-0 items-center gap-1.5 border-b border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={title}>
          {title}
        </span>
        <button
          type="button"
          onClick={createConversation}
          aria-label="New Session"
          title="New Session"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--aisoc-radius-sm)] text-[var(--aisoc-muted)] transition-colors hover:bg-[var(--aisoc-accent-soft)] hover:text-[var(--aisoc-accent)]"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            navigate('/chat');
          }}
          aria-label="Open in full view"
          title="Open in full view"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--aisoc-radius-sm)] text-[var(--aisoc-muted)] transition-colors hover:bg-[var(--aisoc-accent-soft)] hover:text-[var(--aisoc-accent)]"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close chat panel"
          title="Close chat panel"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--aisoc-radius-sm)] text-[var(--aisoc-muted)] transition-colors hover:bg-[var(--aisoc-accent-soft)] hover:text-[var(--aisoc-accent)]"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        <MessageStream />
        <Composer />
      </div>
    </div>
  );
}
