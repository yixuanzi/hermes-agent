import { useEffect, useMemo, useRef, useState } from 'react';
import { Bell, ChevronDown, KeyRound, LogOut, Palette, Settings } from 'lucide-react';
import Sidebar from './components/Sidebar';
import OverviewTab from './components/OverviewTab';
import ChatTab from './components/ChatTab';
import AgentTab from './components/AgentTab';
import PolicyTab from './components/PolicyTab';
import LoginScreen from './components/LoginScreen';
import SsoCallbackScreen from './components/SsoCallbackScreen';
import RegisterScreen from './components/RegisterScreen';
import UserManagementTab from './components/UserManagementTab';
import ChangePasswordDialog from './components/ChangePasswordDialog';
import SettingsTab from './components/SettingsTab';
import AuditLogsTab from './components/AuditLogsTab';
import PromptTemplateTab from './components/PromptTemplateTab';
import UserManualTab from './components/UserManualTab';
import AppEntryTab from './components/AppEntryTab';
import ThemeDialog from './components/ThemeDialog';
import { AegisChatProvider, useAegisChatRuntime } from './lib/chatRuntime';
import { clearStoredAuth, getStoredUser, hasStoredToken, setStoredAuth, setStoredUser } from './lib/auth';
import { fetchJSON, ApiError, alertApiError, getApiErrorMessage } from './lib/api';
import { AegisTheme, applyTheme, getStoredTheme } from './lib/theme';
import {
  BackendAgent,
  BackendAgentList,
  BackendOverviewAgentList,
  BackendRoutingRule,
  BackendRoutingRuleList,
  backendAgentToUi,
  backendRuleToUi,
  uiAgentDraftToApi,
  uiRoutingDraftToApi,
} from './lib/adapters';
import { Agent, AgentDraft, AppEntryList, AuthenticatedUser, OverviewStats, RoutingRule, RoutingRuleDraft, StarmappingTopology, UserDraft } from './types';

type AppTab = 'overview' | 'chat' | 'prompt_templates' | 'user_manual' | 'app_entry' | 'orchestration' | 'policy' | 'users' | 'settings' | 'audit';

type AuthLoginResponse = {
  authenticated: boolean;
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthenticatedUser;
};

type SsoExchangeResponse = AuthLoginResponse;

type AuthSessionResponse = {
  authenticated: boolean;
  expires_in?: number;
  user?: AuthenticatedUser | null;
};

type SystemBootstrapResponse = {
  embedded_chat: boolean;
  auth_scheme: string;
  admin_setup_required: boolean;
  lark_sso_enabled?: boolean;
};

type BackendUserList = {
  users: AuthenticatedUser[];
};

const TAB_TO_PATH: Record<AppTab, string> = {
  overview: '/overview',
  chat: '/chat',
  prompt_templates: '/prompt-templates',
  user_manual: '/user-manual',
  app_entry: '/app-entry',
  orchestration: '/orchestration',
  policy: '/policy',
  users: '/users',
  settings: '/settings',
  audit: '/audit',
};

function isAdminOnlyTab(tab: AppTab | null): boolean {
  return tab === 'orchestration' || tab === 'policy' || tab === 'users' || tab === 'settings' || tab === 'audit';
}

const getUtcTimestamp = () => new Date().toISOString().slice(0, 19).replace('T', ' ');

function resolveTabFromPath(pathname: string): AppTab | null {
  if (pathname === '/login' || pathname === '/register') {
    return null;
  }
  if (pathname === '/chat') {
    return 'chat';
  }
  if (pathname === '/prompt-templates') {
    return 'prompt_templates';
  }
  if (pathname === '/user-manual') {
    return 'user_manual';
  }
  if (pathname === '/app-entry') {
    return 'app_entry';
  }
  if (pathname === '/orchestration') {
    return 'orchestration';
  }
  if (pathname === '/policy') {
    return 'policy';
  }
  if (pathname === '/users') {
    return 'users';
  }
  if (pathname === '/settings') {
    return 'settings';
  }
  if (pathname === '/audit') {
    return 'audit';
  }
  return 'overview';
}

function sortAgents(agents: Agent[]): Agent[] {
  return [...agents].sort((left, right) => left.id.localeCompare(right.id));
}

function sortRules(rules: RoutingRule[]): RoutingRule[] {
  return [...rules]
    .sort((left, right) => left.id.localeCompare(right.id))
    .map((rule, index) => ({ ...rule, priority: index + 1 }));
}

function sortUsers(users: AuthenticatedUser[]): AuthenticatedUser[] {
  return [...users].sort((left, right) => left.username.localeCompare(right.username));
}

function getInitials(user?: AuthenticatedUser | null): string {
  const username = user?.username?.trim() || 'AU';
  return username.slice(0, 2).toUpperCase();
}

function getUserRoleLabel(user?: AuthenticatedUser | null): string {
  return user?.is_admin ? 'Administrator' : 'User';
}

function AuthenticatedAppShell({
  activeTab,
  agents,
  currentUser,
  currentUtcTime,
  isSyncing,
  navigateTo,
  onChangePassword,
  onCreateAgent,
  onCreateRule,
  onCreateUser,
  onDeleteAgent,
  onDeleteRule,
  onDeleteUser,
  onAuthExpired,
  onLogout,
  onRefresh,
  onResetUserPassword,
  onToggleUserStatus,
  onUpdateAgent,
  onUpdateRule,
  overviewAgents,
  overviewStats,
  overviewStatsError,
  topology,
  topologyError,
  appEntries,
  appEntriesLoading,
  appEntriesError,
  appEntriesLoaded,
  onRefreshAppEntries,
  rules,
  syncError,
  theme,
  onThemeChange,
  users,
}: {
  activeTab: AppTab;
  agents: Agent[];
  currentUser: AuthenticatedUser;
  currentUtcTime: string;
  isSyncing: boolean;
  navigateTo: (tab: AppTab) => void;
  onChangePassword: () => void;
  onCreateAgent: (draft: AgentDraft) => Promise<void>;
  onCreateRule: (draft: RoutingRuleDraft) => Promise<void>;
  onCreateUser: (draft: UserDraft) => Promise<void>;
  onDeleteAgent: (agentId: string) => Promise<void>;
  onDeleteRule: (ruleId: string) => Promise<void>;
  onDeleteUser: (uid: string) => Promise<void>;
  onAuthExpired: () => void;
  onLogout: () => void;
  onRefresh: () => Promise<void>;
  onResetUserPassword: (uid: string, password: string) => Promise<void>;
  onToggleUserStatus: (uid: string, status: 'enabled' | 'disabled') => Promise<void>;
  onUpdateAgent: (agentId: string, draft: AgentDraft) => Promise<void>;
  onUpdateRule: (ruleId: string, draft: RoutingRuleDraft) => Promise<void>;
  overviewAgents: Agent[];
  overviewStats: OverviewStats | null;
  overviewStatsError: string;
  topology: StarmappingTopology | null;
  topologyError: string;
  appEntries: AppEntryList | null;
  appEntriesLoading: boolean;
  appEntriesError: string;
  appEntriesLoaded: boolean;
  onRefreshAppEntries: () => Promise<void>;
  rules: RoutingRule[];
  syncError: string;
  theme: AegisTheme;
  onThemeChange: (theme: AegisTheme) => void;
  users: AuthenticatedUser[];
}) {
  const { chatAttentionCount } = useAegisChatRuntime();
  const [menuOpen, setMenuOpen] = useState(false);
  const [themeDialogOpen, setThemeDialogOpen] = useState(false);
  const themeTriggerRef = useRef<HTMLButtonElement>(null);
  const isAdmin = currentUser.is_admin;

  useEffect(() => {
    setMenuOpen(false);
  }, [activeTab]);

  return (
    <div className="flex h-screen w-screen overflow-hidden border border-slate-800 bg-[#020408] font-sans text-slate-300 antialiased">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={(tab) => navigateTo(tab as AppTab)}
        chatAttentionCount={chatAttentionCount}
        isAdmin={isAdmin}
      />

      <div className="relative flex h-screen min-w-0 flex-1 flex-col overflow-hidden bg-[#020408]">
        <header className="z-20 flex h-16 shrink-0 items-center justify-between border-b border-slate-800 bg-[#03060C] px-6">
          <div className="flex items-center space-x-4">
            <span className="text-xs font-mono text-slate-500">PATH: ROOT/{activeTab.toUpperCase()}</span>
            <span className="h-4 w-px bg-slate-800" />
            <span className={`aegis-header-sync ${
              syncError
                ? 'aegis-header-sync--warning'
                : 'aegis-header-sync--success'
            }`}>
              <span className={`aegis-header-sync__indicator ${syncError ? '' : 'animate-pulse'}`} />
              {isSyncing ? 'LIVE_SYNC: SYNCING' : syncError ? 'LIVE_SYNC: DEGRADED' : 'LIVE_SYNC: CONNECTED'}
            </span>
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden items-center gap-1.5 rounded border border-slate-800 bg-[#05080F] px-2.5 py-1 font-mono text-[11px] text-slate-400 shadow-inner md:flex">
              <span className="font-bold text-cyan-400">UTC:</span>
              <span className="text-white">{currentUtcTime}</span>
            </div>
            <button
              ref={themeTriggerRef}
              type="button"
              aria-label="Choose color theme"
              aria-expanded={themeDialogOpen}
              aria-haspopup="dialog"
              title="Choose color theme"
              onClick={() => setThemeDialogOpen(true)}
              className="shrink-0 rounded border border-slate-800 bg-[#05080F] p-1.5 text-slate-400 transition-all hover:bg-[#080C14] hover:text-cyan-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400"
            >
              <Palette className="h-4 w-4" />
            </button>

            <div className="flex items-center gap-2.5">
              <button
                title="Notifications panel"
                className="relative shrink-0 rounded border border-slate-800 bg-[#05080F] p-1.5 text-slate-400 transition-all hover:bg-[#080C14] hover:text-cyan-400"
              >
                <span className="aegis-status-indicator aegis-status-indicator--danger absolute -right-0.5 -top-0.5" />
                <Bell className="h-4 w-4" />
              </button>
              {isAdmin ? (
                <button
                  type="button"
                  title="Configure platform settings"
                  onClick={() => navigateTo('settings')}
                  className="shrink-0 rounded border border-slate-800 bg-[#05080F] p-1.5 text-slate-400 transition-all hover:bg-[#080C14] hover:text-cyan-400"
                >
                  <Settings className="h-4 w-4" />
                </button>
              ) : null}

              <div className="relative flex items-center gap-2.5 border-l border-slate-800 pl-3">
                <button
                  type="button"
                  aria-label="User Menu"
                  onClick={() => setMenuOpen((current) => !current)}
                  className="flex items-center gap-2 rounded-xl border border-slate-800 bg-[#05080F] px-2.5 py-1.5 text-left transition hover:border-cyan-500"
                >
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-600 bg-gradient-to-tr from-slate-700 to-slate-900 p-[1px] font-mono text-[10px] font-bold text-white">
                    {getInitials(currentUser)}
                  </div>
                  <div className="hidden text-left text-[11px] leading-tight sm:block">
                    <div className="font-bold text-white">{currentUser.username}</div>
                    <div className="mt-0.5 text-[9px] font-mono text-[#22d3ee]">
                      {getUserRoleLabel(currentUser)}
                    </div>
                  </div>
                  <ChevronDown className="h-4 w-4 text-slate-500" />
                </button>

                {menuOpen ? (
                  <div className="absolute right-0 top-12 z-30 w-56 rounded-2xl border border-slate-800 bg-[#05080F] p-2 shadow-2xl">
                    <div className="border-b border-slate-800 px-3 py-2">
                      <div className="text-sm font-semibold text-white">{currentUser.username}</div>
                      <div className="mt-1 text-[11px] text-slate-400">{currentUser.email}</div>
                      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 rounded-lg border border-slate-800 bg-[#080C14] px-2 py-1.5">
                        <dt className="font-mono text-[9px] font-bold tracking-wider text-slate-500">USER ID</dt>
                        <dd title={currentUser.uid} className="min-w-0 truncate text-right font-mono text-[10px] text-cyan-300">
                          {currentUser.uid}
                        </dd>
                      </dl>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        onChangePassword();
                      }}
                      className="mt-2 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-slate-300 transition hover:bg-[#080C14] hover:text-white"
                    >
                      <KeyRound className="h-4 w-4" />
                      Change Password
                    </button>
                    <button
                      type="button"
                      onClick={onLogout}
                      className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-rose-300 transition hover:bg-rose-950/30"
                    >
                      <LogOut className="h-4 w-4" />
                      Sign Out
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </header>

        <main className="relative flex flex-1 flex-col overflow-hidden bg-[#020408]" id="main-content-viewport">
          {activeTab === 'overview' ? (
            <OverviewTab
              agents={overviewAgents}
              currentUtcTime={currentUtcTime}
              stats={overviewStats}
              statsError={overviewStatsError}
              topology={topology}
              topologyError={topologyError}
              appEntries={appEntries?.entries ?? []}
              appEntriesLoading={appEntriesLoading}
              appEntriesError={appEntriesError}
              appEntriesLoaded={appEntriesLoaded}
              setTab={(tab) => navigateTo(tab as AppTab)}
            />
          ) : null}
          {activeTab === 'chat' ? <ChatTab agents={agents} /> : null}
          {activeTab === 'prompt_templates' ? <PromptTemplateTab /> : null}
          {activeTab === 'user_manual' ? <UserManualTab /> : null}
          {activeTab === 'app_entry' ? (
            <AppEntryTab
              directory={appEntries}
              loading={appEntriesLoading}
              error={appEntriesError}
              loaded={appEntriesLoaded}
              onRefresh={onRefreshAppEntries}
            />
          ) : null}
          {activeTab === 'orchestration' ? (
            <AgentTab
              agents={agents}
              busy={isSyncing}
              onCreate={onCreateAgent}
              onDelete={onDeleteAgent}
              onRefresh={onRefresh}
              onUpdate={onUpdateAgent}
            />
          ) : null}
          {activeTab === 'policy' ? (
            <PolicyTab
              agents={agents}
              busy={isSyncing}
              onCreate={onCreateRule}
              onDelete={onDeleteRule}
              onRefresh={onRefresh}
              onUpdate={onUpdateRule}
              onAuthExpired={onAuthExpired}
              rules={rules}
            />
          ) : null}
          {activeTab === 'users' ? (
            <UserManagementTab
              busy={isSyncing}
              onCreate={onCreateUser}
              onDelete={onDeleteUser}
              onRefresh={onRefresh}
              onResetPassword={onResetUserPassword}
              onUpdateStatus={onToggleUserStatus}
              users={users}
            />
          ) : null}
          {activeTab === 'settings' ? <SettingsTab onAuthExpired={onAuthExpired} /> : null}
          {activeTab === 'audit' ? <AuditLogsTab onAuthExpired={onAuthExpired} /> : null}
        </main>
      </div>
      {themeDialogOpen ? (
        <ThemeDialog
          selectedTheme={theme}
          onThemeChange={onThemeChange}
          onClose={() => setThemeDialogOpen(false)}
          triggerRef={themeTriggerRef}
        />
      ) : null}
    </div>
  );
}

export default function App() {
  const [pathname, setPathname] = useState(window.location.pathname);
  const [currentUtcTime, setCurrentUtcTime] = useState<string>(() => getUtcTimestamp());
  const [agents, setAgents] = useState<Agent[]>([]);
  const [overviewAgents, setOverviewAgents] = useState<Agent[]>([]);
  const [overviewStats, setOverviewStats] = useState<OverviewStats | null>(null);
  const [overviewStatsError, setOverviewStatsError] = useState('');
  const [topology, setTopology] = useState<StarmappingTopology | null>(null);
  const [topologyError, setTopologyError] = useState('');
  const [appEntries, setAppEntries] = useState<AppEntryList | null>(null);
  const [appEntriesLoading, setAppEntriesLoading] = useState(false);
  const [appEntriesError, setAppEntriesError] = useState('');
  const [appEntriesLoaded, setAppEntriesLoaded] = useState(false);
  const appEntriesRequestRef = useRef<Promise<void> | null>(null);
  const appEntriesRequestGenerationRef = useRef(0);
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [users, setUsers] = useState<AuthenticatedUser[]>([]);
  const [currentUser, setCurrentUser] = useState<AuthenticatedUser | null>(() => getStoredUser());
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [larkSsoEnabled, setLarkSsoEnabled] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [authPending, setAuthPending] = useState(false);
  const [registerPending, setRegisterPending] = useState(false);
  const [passwordPending, setPasswordPending] = useState(false);
  const [authNotice, setAuthNotice] = useState('');
  const [syncError, setSyncError] = useState('');
  const [isSyncing, setIsSyncing] = useState(false);
  const [showPasswordDialog, setShowPasswordDialog] = useState(false);
  const [theme, setTheme] = useState<AegisTheme>(() => getStoredTheme());

  const requestedTab = useMemo(() => resolveTabFromPath(pathname) || 'overview', [pathname]);
  const activeTab = isAdminOnlyTab(requestedTab) && !currentUser?.is_admin
    ? 'overview'
    : requestedTab;

  useEffect(() => {
    const timer = window.setInterval(() => {
      setCurrentUtcTime(getUtcTimestamp());
    }, 1000);

    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const handlePopState = () => {
      setPathname(window.location.pathname);
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    async function bootstrap() {
      setIsBootstrapping(true);
      setSyncError('');

      const initialPath = window.location.pathname;
      const launchParams = new URLSearchParams(window.location.search);
      const organizationId = launchParams.get('organization_id') || '';
      const clientId = launchParams.get('client_id') || '';
      if (initialPath === '/' && (organizationId || clientId)) {
        if (!organizationId || !clientId) {
          window.history.replaceState({}, '', '/login');
          setAuthNotice('服务入口参数不完整，无法发起 OIDC 登录。');
          setPathname('/login');
          setIsBootstrapping(false);
          return;
        }
        const query = new URLSearchParams({ organization_id: organizationId, client_id: clientId });
        window.location.replace(`/api/sso/start?${query.toString()}`);
        return;
      }

      if (initialPath === '/sso/callback') {
        setIsAuthenticated(false);
        setIsBootstrapping(false);
        return;
      }

      const systemBootstrapPromise = fetchJSON<SystemBootstrapResponse>('/api/system/bootstrap', {}, false)
        .then((systemBootstrap) => {
          setLarkSsoEnabled(systemBootstrap.lark_sso_enabled === true);
        })
        .catch(() => {
          setLarkSsoEnabled(false);
        });

      if (!hasStoredToken()) {
        await systemBootstrapPromise;
        setIsAuthenticated(false);
        if (window.location.pathname !== '/register' && window.location.pathname !== '/sso/callback') {
          window.history.replaceState({}, '', '/login');
          setPathname('/login');
        }
        setIsBootstrapping(false);
        return;
      }

      try {
        const [, session] = await Promise.all([
          systemBootstrapPromise,
          fetchJSON<AuthSessionResponse>('/api/auth/session'),
        ]);
        if (!session.authenticated || !session.user) {
          clearStoredAuth();
          resetAppEntries();
          setCurrentUser(null);
          setIsAuthenticated(false);
          window.history.replaceState({}, '', '/login');
          setPathname('/login');
          setIsBootstrapping(false);
          return;
        }

        setStoredUser(session.user);
        setCurrentUser(session.user);
        setIsAuthenticated(true);
        await loadConsoleData(session.user);

        const nextTab = resolveTabFromPath(window.location.pathname);
        if (window.location.pathname === '/login' || window.location.pathname === '/register') {
          window.history.replaceState({}, '', '/overview');
          setPathname('/overview');
        } else {
          setPathname(window.location.pathname);
        }
      } catch (error) {
        clearStoredAuth();
        resetAppEntries();
        setCurrentUser(null);
        setIsAuthenticated(false);
        alertApiError(error, 'Authentication failed.');
        window.history.replaceState({}, '', '/login');
        setPathname('/login');
      } finally {
        setIsBootstrapping(false);
      }
    }

    void bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!currentUser || currentUser.is_admin) {
      return;
    }

    const requestedTab = resolveTabFromPath(pathname);
    if (!isAdminOnlyTab(requestedTab)) {
      return;
    }

    window.alert('Admin access required.');
    window.history.replaceState({}, '', TAB_TO_PATH.overview);
    setPathname(TAB_TO_PATH.overview);
  }, [currentUser, pathname]);

  useEffect(() => {
    if (!isAuthenticated || !currentUser || activeTab !== 'overview') {
      return;
    }

    const timer = window.setInterval(() => {
      void refreshOverviewStats();
    }, 60_000);

    return () => window.clearInterval(timer);
  }, [activeTab, isAuthenticated, currentUser?.uid]);

  useEffect(() => {
    if (!isAuthenticated || !currentUser || (activeTab !== 'overview' && activeTab !== 'app_entry') || appEntriesLoaded) {
      return;
    }

    void loadAppEntries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, appEntriesLoaded, currentUser?.uid, isAuthenticated]);

  function resetAppEntries() {
    appEntriesRequestGenerationRef.current += 1;
    appEntriesRequestRef.current = null;
    setAppEntries(null);
    setAppEntriesLoading(false);
    setAppEntriesError('');
    setAppEntriesLoaded(false);
  }

  async function loadAppEntries(): Promise<void> {
    if (appEntriesRequestRef.current) {
      return appEntriesRequestRef.current;
    }

    const requestGeneration = appEntriesRequestGenerationRef.current;
    const request = (async () => {
      setAppEntriesLoading(true);
      setAppEntriesError('');
      setAppEntriesLoaded(false);
      try {
        const directory = await fetchJSON<AppEntryList>('/api/app-entries');
        if (requestGeneration !== appEntriesRequestGenerationRef.current) {
          return;
        }
        setAppEntries(directory);
        setAppEntriesLoaded(true);
      } catch (error) {
        if (requestGeneration !== appEntriesRequestGenerationRef.current) {
          return;
        }
        if (error instanceof ApiError && error.status === 401) {
          handleAuthExpired();
          return;
        }
        setAppEntriesError(getApiErrorMessage(error, 'Unable to load application entries.'));
      } finally {
        if (requestGeneration === appEntriesRequestGenerationRef.current) {
          setAppEntriesLoading(false);
        }
      }
    })();
    appEntriesRequestRef.current = request;

    try {
      await request;
    } finally {
      if (appEntriesRequestRef.current === request) {
        appEntriesRequestRef.current = null;
      }
    }
  }

  async function loadConsoleData(userOverride?: AuthenticatedUser | null) {
    const activeUser = userOverride ?? currentUser;
    if (!activeUser) {
      return;
    }

    setIsSyncing(true);
    setSyncError('');
    try {
      const [overviewAgentsResult, overviewStatsResult, topologyResult] = await Promise.allSettled([
        fetchJSON<BackendOverviewAgentList>('/api/overview/agents'),
        fetchJSON<OverviewStats>('/api/overview/stats'),
        fetchJSON<StarmappingTopology>('/api/overview/topology'),
      ]);
      if (overviewAgentsResult.status === 'rejected') {
        throw overviewAgentsResult.reason;
      }
      setOverviewAgents(sortAgents(overviewAgentsResult.value.agents.map(backendAgentToUi)));

      if (overviewStatsResult.status === 'fulfilled') {
        setOverviewStats(overviewStatsResult.value);
        setOverviewStatsError('');
      } else if (overviewStatsResult.reason instanceof ApiError && overviewStatsResult.reason.status === 401) {
        handleAuthExpired();
        return;
      } else {
        setOverviewStatsError(getApiErrorMessage(overviewStatsResult.reason, 'Overview metrics unavailable.'));
      }

      if (topologyResult.status === 'fulfilled') {
        setTopology(topologyResult.value);
        setTopologyError('');
      } else if (topologyResult.reason instanceof ApiError && topologyResult.reason.status === 401) {
        handleAuthExpired();
        return;
      } else {
        setTopologyError(getApiErrorMessage(topologyResult.reason, 'Topology data unavailable.'));
      }

      if (!activeUser.is_admin) {
        setAgents([]);
        setRules([]);
        setUsers([]);
        return;
      }

      const [agentResponse, routingResponse, userResponse] = await Promise.all([
        fetchJSON<BackendAgentList>('/api/agents'),
        fetchJSON<BackendRoutingRuleList>('/api/routing/global'),
        fetchJSON<BackendUserList>('/api/users'),
      ]);

      setAgents(sortAgents(agentResponse.agents.map(backendAgentToUi)));
      setRules(sortRules(routingResponse.rules.map(backendRuleToUi)));
      setUsers(sortUsers(userResponse.users));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      setSyncError(getApiErrorMessage(error, 'Failed to sync Aegis console data.'));
      alertApiError(error, 'Failed to sync Aegis console data.');
    } finally {
      setIsSyncing(false);
    }
  }

  function navigateTo(tab: AppTab) {
    if (isAdminOnlyTab(tab) && !currentUser?.is_admin) {
      window.alert('Admin access required.');
      tab = 'overview';
    }
    const nextPath = TAB_TO_PATH[tab];
    setPathname(nextPath);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, '', nextPath);
    }
  }

  function navigateAuth(path: '/login' | '/register') {
    setPathname(path);
    if (window.location.pathname !== path) {
      window.history.replaceState({}, '', path);
    }
  }

  function handleAuthExpired() {
    clearStoredAuth();
    resetAppEntries();
    setCurrentUser(null);
    setIsAuthenticated(false);
    setAgents([]);
    setOverviewAgents([]);
    setOverviewStats(null);
    setOverviewStatsError('');
    setTopology(null);
    setTopologyError('');
    setRules([]);
    setUsers([]);
    setAuthNotice('');
    navigateAuth('/login');
  }

  async function refreshOverviewStats() {
    try {
      const [statsResponse, topologyResponse] = await Promise.all([
        fetchJSON<OverviewStats>('/api/overview/stats'),
        fetchJSON<StarmappingTopology>('/api/overview/topology'),
      ]);
      setOverviewStats(statsResponse);
      setOverviewStatsError('');
      setTopology(topologyResponse);
      setTopologyError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      setOverviewStatsError(getApiErrorMessage(error, 'Overview metrics unavailable.'));
    }
  }

  async function handleLogin(username: string, password: string) {
    setAuthPending(true);
    setAuthNotice('');

    try {
      const response = await fetchJSON<AuthLoginResponse>(
        '/api/auth/login',
        {
          method: 'POST',
          body: JSON.stringify({ username, password }),
        },
        false,
      );

      setStoredAuth(response.access_token, response.user);
      setCurrentUser(response.user);
      setIsAuthenticated(true);
      await loadConsoleData(response.user);
      navigateTo('overview');
    } catch (error) {
      alertApiError(error, 'Authentication failed.');
    } finally {
      setAuthPending(false);
    }
  }

  async function handleSsoComplete(response: SsoExchangeResponse) {
    setStoredAuth(response.access_token, response.user);
    setCurrentUser(response.user);
    setIsAuthenticated(true);
    await loadConsoleData(response.user);
    navigateTo('overview');
  }

  async function handleRegister(username: string, password: string, email: string) {
    setRegisterPending(true);
    setAuthNotice('');
    try {
      await fetchJSON<{ registered: boolean }>(
        '/api/auth/register',
        {
          method: 'POST',
          body: JSON.stringify({ username, password, email }),
        },
        false,
      );
      setAuthNotice('Registration submitted. Wait for an administrator to enable your account.');
      navigateAuth('/login');
    } catch (error) {
      alertApiError(error, 'Registration failed.');
    } finally {
      setRegisterPending(false);
    }
  }

  function handleLogout() {
    clearStoredAuth();
    resetAppEntries();
    setCurrentUser(null);
    setIsAuthenticated(false);
    setAgents([]);
    setOverviewAgents([]);
    setOverviewStats(null);
    setOverviewStatsError('');
    setTopology(null);
    setTopologyError('');
    setRules([]);
    setUsers([]);
    setShowPasswordDialog(false);
    navigateAuth('/login');
  }

  async function handleChangePassword(oldPassword: string, newPassword: string) {
    setPasswordPending(true);
    try {
      await fetchJSON<{ updated: boolean }>(
        '/api/auth/password',
        {
          method: 'PUT',
          body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
        },
      );
      setShowPasswordDialog(false);
      setAuthNotice('Password updated successfully.');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      alertApiError(error, 'Failed to update password.');
    } finally {
      setPasswordPending(false);
    }
  }

  async function handleCreateAgent(draft: AgentDraft) {
    try {
      const created = await fetchJSON<BackendAgent>(
        `/api/agents/${encodeURIComponent(draft.agentId)}`,
        {
          method: 'POST',
          body: JSON.stringify(uiAgentDraftToApi(draft)),
        },
      );
      setAgents((current) => sortAgents([...current, backendAgentToUi(created)]));
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleUpdateAgent(agentId: string, draft: AgentDraft) {
    try {
      const updated = await fetchJSON<BackendAgent>(
        `/api/agents/${encodeURIComponent(agentId)}`,
        {
          method: 'PUT',
          body: JSON.stringify(uiAgentDraftToApi(draft)),
        },
      );
      setAgents((current) =>
        sortAgents(current.map((agent) => (agent.id === agentId ? backendAgentToUi(updated) : agent))),
      );
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleDeleteAgent(agentId: string) {
    try {
      await fetchJSON<{ deleted: boolean }>(
        `/api/agents/${encodeURIComponent(agentId)}`,
        { method: 'DELETE' },
      );
      setAgents((current) => current.filter((agent) => agent.id !== agentId));
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleCreateRule(draft: RoutingRuleDraft) {
    try {
      const created = await fetchJSON<BackendRoutingRule>(
        '/api/routing/global',
        {
          method: 'POST',
          body: JSON.stringify(uiRoutingDraftToApi(draft)),
        },
      );
      setRules((current) => sortRules([...current, backendRuleToUi(created, current.length)]));
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleUpdateRule(ruleId: string, draft: RoutingRuleDraft) {
    try {
      const updated = await fetchJSON<BackendRoutingRule>(
        `/api/routing/global/${encodeURIComponent(ruleId)}`,
        {
          method: 'PUT',
          body: JSON.stringify(uiRoutingDraftToApi(draft)),
        },
      );
      setRules((current) =>
        sortRules(current.map((rule, index) => (rule.id === ruleId ? backendRuleToUi(updated, index) : rule))),
      );
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleDeleteRule(ruleId: string) {
    try {
      await fetchJSON<{ deleted: boolean }>(
        `/api/routing/global/${encodeURIComponent(ruleId)}`,
        { method: 'DELETE' },
      );
      setRules((current) => sortRules(current.filter((rule) => rule.id !== ruleId)));
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleCreateUser(draft: UserDraft) {
    try {
      const created = await fetchJSON<AuthenticatedUser>(
        '/api/users',
        {
          method: 'POST',
          body: JSON.stringify(draft),
        },
      );
      setUsers((current) => sortUsers([...current, created]));
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleDeleteUser(uid: string) {
    try {
      await fetchJSON<{ deleted: boolean }>(`/api/users/${encodeURIComponent(uid)}`, { method: 'DELETE' });
      setUsers((current) => sortUsers(current.filter((user) => user.uid !== uid)));
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleToggleUserStatus(uid: string, status: 'enabled' | 'disabled') {
    try {
      const updated = await fetchJSON<AuthenticatedUser>(
        `/api/users/${encodeURIComponent(uid)}/status`,
        {
          method: 'PUT',
          body: JSON.stringify({ status }),
        },
      );
      setUsers((current) => sortUsers(current.map((user) => (user.uid === uid ? updated : user))));
      if (currentUser?.uid === uid) {
        setCurrentUser(updated);
        setStoredUser(updated);
      }
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  async function handleResetUserPassword(uid: string, password: string) {
    try {
      await fetchJSON<{ updated: boolean }>(
        `/api/users/${encodeURIComponent(uid)}/password`,
        {
          method: 'PUT',
          body: JSON.stringify({ password }),
        },
      );
      setSyncError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        handleAuthExpired();
        return;
      }
      throw error;
    }
  }

  if (isBootstrapping) {
    return (
      <div className="min-h-screen bg-[#020408] text-slate-300 flex items-center justify-center">
        <div className="text-center">
          <div className="mx-auto h-10 w-10 rounded-full border-2 border-cyan-500/20 border-t-cyan-400 animate-spin" />
          <p className="mt-4 text-sm font-mono text-slate-500">Bootstrapping Aegis Console...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated || !currentUser) {
    if (pathname === '/sso/callback') {
      return (
        <SsoCallbackScreen
          onComplete={handleSsoComplete}
          onBackToLogin={() => navigateAuth('/login')}
        />
      );
    }
    if (pathname === '/register') {
      return (
        <RegisterScreen
          onSubmit={handleRegister}
          onSwitchToLogin={() => navigateAuth('/login')}
          pending={registerPending}
        />
      );
    }

    return (
      <LoginScreen
        notice={authNotice}
        onSubmit={handleLogin}
        onAegisSsoLogin={() => window.location.assign('/api/sso/start?sso=1')}
        onLarkSsoLogin={() => window.location.assign('/api/lark/start')}
        larkSsoEnabled={larkSsoEnabled}
        onSwitchToRegister={() => navigateAuth('/register')}
        pending={authPending}
      />
    );
  }

  return (
    <>
      <ChangePasswordDialog
        open={showPasswordDialog}
        onClose={() => {
          setShowPasswordDialog(false);
        }}
        onSubmit={handleChangePassword}
        pending={passwordPending}
      />
      <AegisChatProvider isChatVisible={activeTab === 'chat'}>
        <AuthenticatedAppShell
          activeTab={activeTab}
          agents={agents}
          currentUser={currentUser}
          currentUtcTime={currentUtcTime}
          isSyncing={isSyncing}
          navigateTo={navigateTo}
          onChangePassword={() => setShowPasswordDialog(true)}
          onCreateAgent={handleCreateAgent}
          onCreateRule={handleCreateRule}
          onCreateUser={handleCreateUser}
          onDeleteAgent={handleDeleteAgent}
          onDeleteRule={handleDeleteRule}
          onDeleteUser={handleDeleteUser}
          onAuthExpired={handleAuthExpired}
          onLogout={handleLogout}
          onRefresh={() => loadConsoleData(currentUser)}
          onResetUserPassword={handleResetUserPassword}
          onToggleUserStatus={handleToggleUserStatus}
          onUpdateAgent={handleUpdateAgent}
          onUpdateRule={handleUpdateRule}
          overviewAgents={overviewAgents}
          overviewStats={overviewStats}
          overviewStatsError={overviewStatsError}
          topology={topology}
          topologyError={topologyError}
          appEntries={appEntries}
          appEntriesLoading={appEntriesLoading}
          appEntriesError={appEntriesError}
          appEntriesLoaded={appEntriesLoaded}
          onRefreshAppEntries={loadAppEntries}
          rules={rules}
          syncError={syncError}
          theme={theme}
          onThemeChange={setTheme}
          users={users}
        />
      </AegisChatProvider>
    </>
  );
}
