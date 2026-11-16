/**
 * 统一聊天模块三栏骨架：
 * 左（会话列表，可折叠为一条窄边）| 中（消息流 + composer）| 右（drawer，可开关 420px）。
 * 展开态的折叠按钮收在 SessionListPane 头部；折叠态下本组件渲染一条极窄常驻边，
 * 放展开按钮——否则 SessionListPane 整块不渲染，折叠按钮会跟着消失，无法再展开。
 */
import { PanelLeftOpen } from 'lucide-react';

interface ChatLayoutProps {
  /** 左栏内容（SessionListPane），仅展开时渲染 */
  sidebar: React.ReactNode;
  sidebarCollapsed?: boolean;
  /** 折叠态窄边上的展开按钮 */
  onToggleSidebar?: () => void;
  /** 中栏内容（MessageStream + RunStateIndicator + Composer） */
  children: React.ReactNode;
  /** 右侧 drawer 内容（ChatDrawer），仅在 drawerOpen 时渲染 */
  drawer?: React.ReactNode;
  drawerOpen?: boolean;
  /** drawer 全屏：隐藏左侧会话列表 + 中间聊天，drawer 占满整个内容区 */
  drawerFullscreen?: boolean;
}

export function ChatLayout({
  sidebar,
  sidebarCollapsed = false,
  onToggleSidebar,
  children,
  drawer,
  drawerOpen = false,
  drawerFullscreen = false,
}: ChatLayoutProps) {
  const showDrawer = drawerOpen && Boolean(drawer);
  const isFullscreen = showDrawer && drawerFullscreen;

  return (
    <div className="chat-shell flex h-full min-h-0 w-full flex-1 overflow-hidden bg-[var(--aisoc-bg)] text-[var(--aisoc-text)]">
      {!isFullscreen ? (
        <>
          {/* 左：会话列表（折叠态收缩为一条窄边） */}
          <aside
            aria-label="Session list"
            className={`relative flex h-full shrink-0 flex-col overflow-hidden border-r border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] transition-[width] duration-150 ${
              sidebarCollapsed ? 'w-7' : 'w-[260px]'
            }`}
          >
            {sidebarCollapsed ? (
              <button
                type="button"
                onClick={onToggleSidebar}
                aria-label="Expand session list"
                aria-expanded={false}
                title="Expand session list"
                className="flex h-7 w-7 items-center justify-center text-[var(--aisoc-muted)] transition-colors hover:bg-[var(--aisoc-panel-strong)] hover:text-[var(--aisoc-accent)]"
              >
                <PanelLeftOpen className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : (
              sidebar
            )}
          </aside>

          {/* 中：消息流 + composer */}
          <section aria-label="Conversation" className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
            {children}
          </section>
        </>
      ) : null}

      {/* 右：drawer（Workflow / 文件预览）；全屏态占满整行 */}
      {showDrawer ? (
        <aside
          aria-label="Session sidebar"
          className={`flex h-full shrink-0 flex-col overflow-hidden border-l border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] ${
            isFullscreen ? 'w-full' : 'w-[420px]'
          }`}
        >
          {drawer}
        </aside>
      ) : null}
    </div>
  );
}
