/**
 * 右侧抽屉：固定 "Workflow" tab + 动态文件 tab（chatDrawerTabs.ts 按会话持久化，
 * 状态由 ChatPage 掌管，本组件纯渲染）。文件 tab 内容用 DrawerFilePreview。
 */
import { FileText, Maximize2, Minimize2, Workflow, X } from 'lucide-react';

import {
  CachedDynamicDrawerTab,
  CachedWorkflowDrawerTab,
} from '../../lib/chatDrawerTabs';
import { Conversation } from '../../types';
import { DrawerFilePreview } from './DrawerFilePreview';
import { WorkflowPanel } from './WorkflowPanel';

interface ChatDrawerProps {
  conversation?: Conversation;
  tabs: CachedDynamicDrawerTab[];
  activeTab: CachedWorkflowDrawerTab;
  /** 同路径文件的刷新计数（agent 重写文件后强制重拉），key 为 tab id */
  refreshNonceByTab?: Record<string, number>;
  onSelectTab: (tab: CachedWorkflowDrawerTab) => void;
  onClose: () => void;
  /** 侧栏是否全屏（隐藏会话列表 + 聊天区，Workflow/文件预览占满整页） */
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
}

export function ChatDrawer({
  conversation,
  tabs,
  activeTab,
  refreshNonceByTab = {},
  onSelectTab,
  onClose,
  fullscreen = false,
  onToggleFullscreen,
}: ChatDrawerProps) {
  const activeDynamicTab = tabs.find((tab) => tab.id === activeTab);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-[var(--aisoc-border)] px-2 py-1.5">
        <button
          type="button"
          onClick={() => onSelectTab('workflow')}
          aria-selected={activeTab === 'workflow'}
          role="tab"
          className={`flex shrink-0 items-center gap-1.5 rounded-[var(--aisoc-radius-sm)] px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-wider transition-colors ${
            activeTab === 'workflow'
              ? 'bg-[var(--aisoc-accent-soft)] text-[var(--aisoc-accent)]'
              : 'text-[var(--aisoc-muted)] hover:bg-[var(--aisoc-panel)] hover:text-[var(--aisoc-text)]'
          }`}
        >
          <Workflow className="h-3 w-3" aria-hidden="true" />
          Workflow
        </button>
        {tabs.map((tab) => {
          const active = tab.id === activeTab;
          return (
            <span
              key={tab.id}
              className={`flex max-w-[180px] shrink-0 items-center gap-1 rounded-[var(--aisoc-radius-sm)] transition-colors ${
                active
                  ? 'bg-[var(--aisoc-accent-soft)] text-[var(--aisoc-accent)]'
                  : 'text-[var(--aisoc-muted)] hover:bg-[var(--aisoc-panel)] hover:text-[var(--aisoc-text)]'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelectTab(tab.id)}
                role="tab"
                aria-selected={active}
                title={tab.path}
                className="flex min-w-0 items-center gap-1.5 px-2 py-1 font-mono text-[10px] font-bold"
              >
                <FileText className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="truncate">{tab.title}</span>
              </button>
            </span>
          );
        })}
        {onToggleFullscreen ? (
          <button
            type="button"
            onClick={onToggleFullscreen}
            aria-label={fullscreen ? 'Restore split view' : 'Fullscreen'}
            aria-pressed={fullscreen}
            title={fullscreen ? 'Restore split view' : 'Fullscreen'}
            className="ml-auto flex h-6 w-6 shrink-0 items-center justify-center rounded-[var(--aisoc-radius-sm)] text-[var(--aisoc-muted)] transition-colors hover:bg-[var(--aisoc-panel)] hover:text-[var(--aisoc-text)]"
          >
            {fullscreen ? (
              <Minimize2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          title="Close panel"
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-[var(--aisoc-radius-sm)] text-[var(--aisoc-muted)] transition-colors hover:bg-[var(--aisoc-panel)] hover:text-[var(--aisoc-text)] ${
            onToggleFullscreen ? '' : 'ml-auto'
          }`}
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === 'workflow' || !activeDynamicTab ? (
          <WorkflowPanel conversation={conversation} />
        ) : (
          <DrawerFilePreview
            key={activeDynamicTab.id}
            path={activeDynamicTab.path}
            refreshNonce={refreshNonceByTab[activeDynamicTab.id] || 0}
          />
        )}
      </div>
    </div>
  );
}
