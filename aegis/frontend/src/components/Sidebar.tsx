import {
  LayoutDashboard,
  MessageSquare,
  Route,
  Sliders,
  Users,
  Workflow,
  FileText,
  ChevronDown,
  ChevronRight,
  FilePenLine,
  BookOpen,
  UserRound,
  AppWindow,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { useState } from 'react';
import aegisLogo from '../../logo/aegis-icon-brand-tile-color.svg';

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  chatAttentionCount?: number;
  isAdmin?: boolean;
}

export default function Sidebar({
  activeTab,
  setActiveTab,
  chatAttentionCount = 0,
  isAdmin = false,
}: SidebarProps) {
  const [userProfileOpen, setUserProfileOpen] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const navItems = [
    { id: 'overview', label: 'Overview', desc: '中控概览', icon: LayoutDashboard },
    { id: 'chat', label: 'Aegis Chat', desc: '智能安全对话', icon: MessageSquare },
  ];

  const adminItems = [
    isAdmin ? { id: 'orchestration', label: 'Agent Orchestration', sub: '工作智能体编排', icon: Workflow, disabled: false } : null,
    isAdmin ? { id: 'policy', label: 'Routing Policy', sub: '路由策略配置', icon: Route, disabled: false } : null,
    isAdmin ? { id: 'users', label: 'User Management', sub: '用户管理', icon: Users, disabled: false } : null,
    isAdmin ? { id: 'settings', label: 'System Settings', sub: '系统设置', icon: Sliders, disabled: false } : null,
    isAdmin ? { id: 'audit', label: 'Audit Logs', sub: '审计日志', icon: FileText, disabled: false } : null,
  ].filter(Boolean) as Array<{
    id: string;
    label: string;
    sub: string;
    icon: typeof Users;
    disabled: boolean;
  }>;

  const handleUserProfileClick = () => {
    if (isCollapsed) {
      setIsCollapsed(false);
      setUserProfileOpen(true);
      return;
    }
    setUserProfileOpen((current) => !current);
  };

  const sidebarToggle = (
    <button
      type="button"
      aria-label={isCollapsed ? 'Expand navigation' : 'Collapse navigation'}
      aria-expanded={!isCollapsed}
      title={isCollapsed ? 'Expand navigation' : 'Collapse navigation'}
      onClick={() => setIsCollapsed((current) => !current)}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-slate-800 text-slate-500 transition-colors hover:border-cyan-900/70 hover:bg-cyan-950/20 hover:text-cyan-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400"
    >
      {isCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
    </button>
  );

  return (
    <aside
      id="sidebar-container"
      className={`flex h-screen shrink-0 flex-col overflow-hidden border-r border-slate-800 bg-[#05080F] transition-[width] duration-200 ease-out ${
        isCollapsed ? 'w-16' : 'w-64'
      }`}
    >
      <div className={`shrink-0 border-b border-slate-800 bg-[#05080F] ${isCollapsed ? 'p-2' : 'p-6'}`}>
        <div className={`flex items-center ${isCollapsed ? 'min-h-[72px] justify-center' : 'min-h-[72px] gap-4'}`}>
          <div className={`relative shrink-0 overflow-hidden border border-cyan-950/60 bg-[#09101B] aegis-status-glow--accent ${isCollapsed ? 'h-10 w-10 rounded-xl' : 'h-16 w-16 rounded-2xl'}`}>
            <img
              id="aegis-logo-icon"
              src={aegisLogo}
              alt="Aegis logo"
              className="aegis-drop-glow--accent h-full w-full scale-[1.03] object-contain"
            />
          </div>
          {!isCollapsed ? (
            <div>
              <span className="text-xl font-bold tracking-tight text-white uppercase italic leading-none">Aegis</span>
              <p className="text-[9px] text-slate-500 font-mono tracking-tighter uppercase mt-0.5">Unified Co-Pilot</p>
            </div>
          ) : null}
        </div>
      </div>

      <nav
        aria-label="Primary navigation"
        className={`min-h-0 flex-1 overflow-y-auto scrollbar-thin ${isCollapsed ? 'px-1 py-4' : 'px-1.5 py-4'}`}
        data-testid="sidebar-menu-scroll-region"
      >
        {!isCollapsed ? <div className="px-4 mb-2 text-[10px] font-mono font-bold tracking-widest text-slate-500 uppercase">CORE MODULES</div> : null}
        <div className="space-y-0.5 mb-6">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                aria-label={item.label}
                title={isCollapsed ? item.label : undefined}
                className={`group relative flex w-full items-center text-left transition-all duration-150 ${
                  isActive
                    ? 'aegis-nav-item--active border-l-2 border-cyan-500 text-cyan-400 font-medium'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-[#080C14] border-l-2 border-transparent'
                } ${isCollapsed ? 'justify-center px-2 py-3' : 'gap-3 px-4 py-3'}`}
              >
                <Icon className={`h-4.5 w-4.5 shrink-0 ${isActive ? 'text-cyan-400' : 'text-slate-500 group-hover:text-slate-300'}`} />
                {!isCollapsed ? (
                  <div className="flex-1">
                    <div className={`text-xs font-semibold tracking-wide ${isActive ? 'text-cyan-400' : 'text-slate-400 group-hover:text-white'}`}>{item.label}</div>
                    <div className="text-[10px] text-slate-500 font-normal leading-tight mt-0.5">{item.desc}</div>
                  </div>
                ) : null}
                {item.id === 'chat' && chatAttentionCount > 0 ? (
                  <span
                    aria-label={`Chat attention count: ${chatAttentionCount}`}
                    className={`aegis-status-badge aegis-status-badge--warning aegis-status-badge--compact ${isCollapsed ? 'absolute right-0.5 top-1' : ''}`}
                  >
                    {chatAttentionCount}
                  </span>
                ) : null}
                {isActive && !isCollapsed ? (
                  <span className="aegis-status-indicator aegis-status-indicator--accent absolute right-3 top-1/2 -translate-y-1/2" />
                ) : null}
              </button>
            );
          })}
          <button
            type="button"
            aria-label="User Profile"
            aria-expanded={!isCollapsed && userProfileOpen}
            aria-controls="user-profile-menu"
            onClick={handleUserProfileClick}
            title={isCollapsed ? 'User Profile' : undefined}
            className={`flex w-full items-center border-l-2 border-transparent text-left text-slate-500 transition-all hover:bg-[#080C14] hover:text-slate-300 ${isCollapsed ? 'justify-center px-2 py-3' : 'gap-3 px-4 py-3'}`}
          >
            <UserRound className="h-4 w-4 shrink-0" />
            {!isCollapsed ? <div className="flex-1"><div className="text-xs font-semibold text-slate-400">User Profile</div><div className="mt-0.5 text-[10px] text-slate-500">用户偏好</div></div> : null}
            {!isCollapsed ? (userProfileOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />) : null}
          </button>
          {!isCollapsed && userProfileOpen ? (
            <div id="user-profile-menu" className="ml-7 border-l border-slate-800 pl-2">
              <button
                type="button"
                onClick={() => setActiveTab('prompt_templates')}
                className={`w-full rounded px-3 py-2.5 text-left flex items-center gap-2 transition ${activeTab === 'prompt_templates' ? 'bg-cyan-950/20 text-cyan-300' : 'text-slate-500 hover:bg-[#080C14] hover:text-slate-300'}`}
              >
                <FilePenLine className="h-3.5 w-3.5" />
                <div><div className="text-xs font-medium">Prompt Template</div><div className="mt-0.5 text-[9px] text-slate-600">提示词模板</div></div>
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('user_manual')}
                className={`w-full rounded px-3 py-2.5 text-left flex items-center gap-2 transition ${activeTab === 'user_manual' ? 'bg-cyan-950/20 text-cyan-300' : 'text-slate-500 hover:bg-[#080C14] hover:text-slate-300'}`}
              >
                <BookOpen className="h-3.5 w-3.5" />
                <div><div className="text-xs font-medium">User Manual</div><div className="mt-0.5 text-[9px] text-slate-600">用户手册</div></div>
              </button>
            </div>
          ) : null}
          <button
            type="button"
            aria-label="App Entry"
            onClick={() => setActiveTab('app_entry')}
            title={isCollapsed ? 'App Entry' : undefined}
            className={`group relative flex w-full items-center text-left transition-all duration-150 ${
              activeTab === 'app_entry'
                ? 'aegis-nav-item--active border-l-2 border-cyan-500 text-cyan-400 font-medium'
                : 'text-slate-500 hover:bg-[#080C14] hover:text-slate-300 border-l-2 border-transparent'
            } ${isCollapsed ? 'justify-center px-2 py-3' : 'gap-3 px-4 py-3'}`}
          >
            <AppWindow className={`h-4 w-4 shrink-0 ${activeTab === 'app_entry' ? 'text-cyan-400' : 'text-slate-500 group-hover:text-slate-300'}`} />
            {!isCollapsed ? (
              <div className="flex-1">
                <div className={`text-xs font-semibold tracking-wide ${activeTab === 'app_entry' ? 'text-cyan-400' : 'text-slate-400 group-hover:text-white'}`}>App Entry</div>
                <div className="mt-0.5 text-[10px] font-normal leading-tight text-slate-500">组织应用入口</div>
              </div>
            ) : null}
            {activeTab === 'app_entry' && !isCollapsed ? (
              <span className="aegis-status-indicator aegis-status-indicator--accent absolute right-3 top-1/2 -translate-y-1/2" />
            ) : null}
          </button>
        </div>

        {adminItems.length > 0 ? (
          <>
            {!isCollapsed ? <div className="px-4 mb-2 text-[10px] font-mono font-bold tracking-widest text-slate-500 uppercase">CONTROL</div> : null}
            <div className="space-y-0.5">
              {adminItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            if (item.disabled) {
              return (
                <div
                  key={item.id}
                  className={`group flex w-full cursor-not-allowed items-center text-left text-slate-500 transition-colors duration-150 hover:text-slate-300 ${isCollapsed ? 'justify-center px-2 py-3' : 'gap-3 px-4 py-2'}`}
                  title={isCollapsed ? item.label : undefined}
                >
                  <Icon className="h-4 w-4 shrink-0 text-slate-600 group-hover:text-cyan-500" />
                  {!isCollapsed ? <div className="flex-1">
                    <div className="text-xs font-medium text-slate-400 group-hover:text-white">{item.label}</div>
                    <div className="text-[9px] text-slate-600 font-mono mt-0.5">{item.sub}</div>
                  </div> : null}
                </div>
              );
            }

            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                aria-label={item.label}
                title={isCollapsed ? item.label : undefined}
                className={`group relative flex w-full items-center text-left transition-all duration-150 ${
                  isActive
                    ? 'aegis-nav-item--active border-l-2 border-cyan-500 text-cyan-400 font-medium'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-[#080C14] border-l-2 border-transparent'
                } ${isCollapsed ? 'justify-center px-2 py-3' : 'gap-3 px-4 py-3'}`}
              >
                <Icon className={`h-4 w-4 shrink-0 ${isActive ? 'text-cyan-400' : 'text-slate-500 group-hover:text-slate-300'}`} />
                {!isCollapsed ? <div className="flex-1">
                  <div className={`text-xs font-semibold tracking-wide ${isActive ? 'text-cyan-400' : 'text-slate-400 group-hover:text-white'}`}>{item.label}</div>
                  <div className="text-[10px] text-slate-500 font-normal leading-tight mt-0.5">{item.sub}</div>
                </div> : null}
              </button>
            );
              })}
            </div>
          </>
        ) : null}
      </nav>

      <div
        className={`shrink-0 border-t border-slate-800/50 bg-[#03060C] ${isCollapsed ? 'p-2' : 'p-6'}`}
        data-testid="sidebar-integrity-footer"
      >
        {!isCollapsed ? (
          <>
            <div className="mb-2 flex items-center justify-between text-[10px] font-bold tracking-widest text-slate-500 uppercase">
              <span>System Integrity</span>
              <span className="aegis-status-text--success font-mono">Secure</span>
            </div>
            <div className="mb-4 h-1 overflow-hidden rounded-full bg-slate-800">
              <div className="aegis-status-glow--accent h-full w-3/4 bg-cyan-500"></div>
            </div>
            <div className="flex items-center gap-2.5" data-testid="sidebar-hub-row">
              <img
                src={aegisLogo}
                alt="Aegis logo"
                className="h-4 w-4 shrink-0 object-contain"
              />
              <div className="min-w-0 flex-1 overflow-hidden">
                <div className="truncate text-[11px] font-bold leading-none text-white">Aegis Hub</div>
                <div className="mt-1 truncate font-mono text-[9px] text-slate-500">v0.2.3-IDENTITY</div>
              </div>
              {sidebarToggle}
            </div>
          </>
        ) : (
          <div className="mb-3 flex justify-center" title="System integrity: secure">
            <span aria-label="System integrity: secure" className="aegis-status-indicator aegis-status-indicator--success aegis-status-glow--success" />
          </div>
        )}
        {isCollapsed ? <div className="flex justify-center border-t border-slate-800/70 pt-2">{sidebarToggle}</div> : null}
      </div>
    </aside>
  );
}
