/**
 * 会话列表栏：新建 / 刷新 / 列表项（标题、时间、未读点、审批与澄清徽标、删除）。
 * 状态全部来自 useChatRuntime。
 */
import { ArrowLeftFromLine, ArrowRightToLine, LoaderCircle, PanelLeftClose, Plus, RefreshCw, Trash2 } from 'lucide-react';

import { useChatRuntime } from '../runtime/chatRuntime';
import { Conversation } from '../types';

function conversationBadge(conversation: Conversation): string | null {
  if (conversation.pendingApproval) return 'Pending approval';
  if (conversation.pendingClarify) return 'Pending clarification';
  return null;
}

interface SessionListPaneProps {
  /** 右侧 Workflow 侧栏当前是否展开（用于渲染开关按钮的图标/状态） */
  drawerOpen?: boolean;
  /** 打开/关闭右侧 Workflow 侧栏 */
  onToggleDrawer?: () => void;
  /** 折叠本栏（展开态下的开关按钮；折叠态下的展开按钮在 ChatLayout 里） */
  onToggleSidebar?: () => void;
}

export function SessionListPane({ drawerOpen, onToggleDrawer, onToggleSidebar }: SessionListPaneProps = {}) {
  const {
    conversations,
    activeConvId,
    sessionsLoading,
    setActiveConversation,
    createConversation,
    deleteConversation,
    refreshSessions,
  } = useChatRuntime();

  function handleDelete(event: React.MouseEvent, conversation: Conversation) {
    event.stopPropagation();
    if (window.confirm(`Delete session "${conversation.title}"? This action cannot be undone.`)) {
      deleteConversation(conversation.id);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-[var(--aisoc-border)] px-3 py-2.5">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.22em] text-[var(--aisoc-muted)]">
          Sessions
        </span>
        <span className="ml-auto" />
        <button
          type="button"
          onClick={() => void refreshSessions()}
          disabled={sessionsLoading}
          aria-label="Refresh session list"
          title="Refresh session list"
          className="flex h-6 w-6 items-center justify-center rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] text-[var(--aisoc-muted)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:text-[var(--aisoc-text)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${sessionsLoading ? 'animate-spin' : ''}`} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={createConversation}
          aria-label="New Session"
          title="New Session"
          className="flex h-6 items-center gap-1 rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] px-1.5 font-mono text-[10px] font-bold text-[var(--aisoc-accent)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:bg-[var(--aisoc-accent-soft)]"
        >
          <Plus className="h-3 w-3" aria-hidden="true" />
          New
        </button>
        {onToggleDrawer ? (
          <button
            type="button"
            onClick={onToggleDrawer}
            aria-label={drawerOpen ? 'Close sidebar' : 'Open sidebar'}
            aria-expanded={drawerOpen}
            title={drawerOpen ? 'Close sidebar' : 'Open sidebar'}
            className="flex h-6 w-6 items-center justify-center rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] text-[var(--aisoc-muted)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:text-[var(--aisoc-text)]"
          >
            {drawerOpen ? (
              <ArrowRightToLine className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ArrowLeftFromLine className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
        ) : null}
        {onToggleSidebar ? (
          <button
            type="button"
            onClick={onToggleSidebar}
            aria-label="Collapse session list"
            aria-expanded={true}
            title="Collapse session list"
            className="flex h-6 w-6 items-center justify-center rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] text-[var(--aisoc-muted)] transition-colors hover:border-[var(--aisoc-border-strong)] hover:text-[var(--aisoc-text)]"
          >
            <PanelLeftClose className="h-3 w-3" aria-hidden="true" />
          </button>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {sessionsLoading && conversations.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-4 font-mono text-[10px] uppercase tracking-wider text-[var(--aisoc-muted)]" role="status">
            <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />
            Loading sessions…
          </div>
        ) : null}
        {!sessionsLoading && conversations.length === 0 ? (
          <p className="px-3 py-4 text-xs text-[var(--aisoc-muted)]">
            No sessions yet. Click "New" to start a conversation.
          </p>
        ) : null}
        <ul>
          {conversations.map((conversation) => {
            const active = conversation.id === activeConvId;
            const badge = conversationBadge(conversation);
            return (
              <li key={conversation.id}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setActiveConversation(conversation.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setActiveConversation(conversation.id);
                    }
                  }}
                  aria-current={active ? 'true' : undefined}
                  className={`group relative flex w-full cursor-pointer items-start gap-2 px-3 py-2 text-left transition-colors ${
                    active
                      ? 'bg-[var(--aisoc-panel-strong)]'
                      : 'hover:bg-[var(--aisoc-panel)]'
                  }`}
                >
                  {/* active 左侧 2px accent 信号条 */}
                  {active ? (
                    <span
                      aria-hidden="true"
                      className="absolute inset-y-0 left-0 w-[2px] bg-[var(--aisoc-accent)]"
                    />
                  ) : null}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      {conversation.hasUnread ? (
                        <span
                          aria-label="Has unread messages"
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--aisoc-accent)]"
                        />
                      ) : null}
                      <span
                        className={`truncate text-xs ${
                          active ? 'font-semibold text-[var(--aisoc-text)]' : 'text-[var(--aisoc-muted)] group-hover:text-[var(--aisoc-text)]'
                        }`}
                      >
                        {conversation.title}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5">
                      <span className="font-mono text-[10px] tabular-nums text-[var(--aisoc-muted)]">
                        {conversation.timestamp}
                      </span>
                      {badge ? (
                        <span className="rounded-full border border-[color-mix(in_srgb,var(--aisoc-warning)_30%,transparent)] bg-[color-mix(in_srgb,var(--aisoc-warning)_10%,transparent)] px-1.5 font-mono text-[9px] font-bold text-[var(--aisoc-warning)]">
                          {badge}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(event) => handleDelete(event, conversation)}
                    aria-label={`Delete session ${conversation.title}`}
                    title="Delete session"
                    className="mt-0.5 hidden h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--aisoc-muted)] transition-colors hover:text-[var(--aisoc-danger)] group-hover:flex"
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
