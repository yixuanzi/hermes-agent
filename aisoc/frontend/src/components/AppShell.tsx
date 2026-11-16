import { Link, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";

import { FloatingChatWidget } from "../chat/FloatingChatWidget";
import { AisocChatProvider, useOptionalChatRuntime } from "../chat/runtime/chatRuntime";
import { clearStoredAuth } from "../lib/auth";
import { useCurrentUser } from "../lib/authContext";
import { HudGrid } from "./ambient/HudGrid";
import { BrandMark } from "./BrandMark";
import { applyTheme, readTheme, type ThemeMode } from "../design/theme";

type NavIconName =
  | "overview"
  | "chat"
  | "sessions"
  | "cron"
  | "skills"
  | "wiki"
  | "memory"
  | "ontology"
  | "integrator"
  | "settings"
  | "users";

interface NavChild {
  path: string;
  label: string;
  /** External destinations render as a plain <a target="_blank"> instead of a router <Link>. */
  external?: boolean;
}

interface NavEntry {
  path: string;
  label: string;
  icon: NavIconName;
  children?: NavChild[];
}

const BASE_NAV_ITEMS: NavEntry[] = [
  { path: "/overview", label: "Overview", icon: "overview" },
  { path: "/chat", label: "Chat", icon: "chat" },
  { path: "/sessions", label: "Sessions", icon: "sessions" },
  { path: "/cron", label: "Cron", icon: "cron" },
  { path: "/skills", label: "Skills", icon: "skills" },
  { path: "/wiki", label: "LLMWiki", icon: "wiki" },
  { path: "/memory", label: "Memory", icon: "memory" },
  {
    path: "/ontology",
    label: "Ontology",
    icon: "ontology",
    children: [
      { path: "/ontology/standard-graph", label: "Standard Graph" },
      { path: "/ontology/diff-overview", label: "Diff Overview" },
    ],
  },
  {
    path: "/integrator",
    label: "Integrator",
    icon: "integrator",
    children: [
      { path: "/integrator/mcp-servers", label: "MCP Servers" },
      { path: "/integrator/skillhub", label: "SkillHub" },
    ],
  },
  { path: "/settings", label: "Settings", icon: "settings" },
];

const ADMIN_NAV_ITEM: NavEntry = { path: "/users", label: "Users", icon: "users" };

function buildNavItems(isAdmin: boolean): NavEntry[] {
  return isAdmin ? [...BASE_NAV_ITEMS, ADMIN_NAV_ITEM] : BASE_NAV_ITEMS;
}

const NAV_COLLAPSED_STORAGE_KEY = "aisoc_nav_collapsed";
const NAV_EXPANDED_PARENTS_KEY = "aisoc_nav_expanded_parents";

function NavIcon({ name }: { name: NavIconName }) {
  const common = {
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    fill: "none",
  };

  return (
    <svg viewBox="0 0 24 24" className="nav-icon-svg" aria-hidden="true" focusable="false">
      {name === "overview" && (
        <>
          <path {...common} d="M4.5 4.5h6v6h-6zM13.5 4.5h6v6h-6zM4.5 13.5h6v6h-6zM13.5 13.5h6v6h-6z" />
        </>
      )}
      {name === "chat" && (
        <>
          <path
            {...common}
            d="M4 6.75a2.75 2.75 0 0 1 2.75-2.75h10.5A2.75 2.75 0 0 1 20 6.75v6.5A2.75 2.75 0 0 1 17.25 16H11l-3.5 3v-3H6.75A2.75 2.75 0 0 1 4 13.25v-6.5Z"
          />
        </>
      )}
      {name === "sessions" && (
        <>
          <path {...common} d="M12 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
          <path {...common} d="M6.5 19.25a5.5 5.5 0 0 1 11 0" />
          <path {...common} d="M6.75 11.75A2.25 2.25 0 1 0 6.75 7.25M17.25 11.75A2.25 2.25 0 1 1 17.25 7.25" />
        </>
      )}
      {name === "cron" && (
        <>
          <path {...common} d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z" />
          <path {...common} d="M12 8v4l2.75 1.75" />
        </>
      )}
      {name === "skills" && (
        <>
          <path
            {...common}
            d="M14.5 4.5a3 3 0 1 1 4.24 4.24L10 17.5 6 18.5l1-4 7.5-10ZM13.5 7.5l3 3"
          />
        </>
      )}
      {name === "wiki" && (
        <>
          <path {...common} d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5Z" />
          <path {...common} d="M8 7h8M8 11h6M8 15h4" />
        </>
      )}
      {name === "memory" && (
        <>
          <path {...common} d="M3.75 7.5c0-1.66 3.7-3 8.25-3s8.25 1.34 8.25 3-3.7 3-8.25 3-8.25-1.34-8.25-3Z" />
          <path {...common} d="M3.75 7.5V16.5c0 1.66 3.7 3 8.25 3s8.25-1.34 8.25-3V7.5" />
          <path {...common} d="M3.75 12c0 1.66 3.7 3 8.25 3s8.25-1.34 8.25-3" />
        </>
      )}
      {name === "ontology" && (
        <>
          <path {...common} d="M12 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5Z" />
          <path {...common} d="M5 15.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5ZM19 15.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5Z" />
          <path {...common} d="M10.25 7.5 6.5 14M13.75 7.5 17.5 14M7 18h10" />
        </>
      )}
      {name === "integrator" && (
        <>
          <path {...common} d="M9 4v3.5M15 4v3.5" />
          <path {...common} d="M6.5 7.5h11v3a5.5 5.5 0 0 1-11 0v-3Z" />
          <path {...common} d="M12 16v2.5M8.5 20.5h7" />
        </>
      )}
      {name === "settings" && (
        <>
          <path {...common} d="M12 15.25A3.25 3.25 0 1 0 12 8.75a3.25 3.25 0 0 0 0 6.5Z" />
          <path {...common} d="M19.1 13.6a7.6 7.6 0 0 0 .05-1.6 7.6 7.6 0 0 0-.05-1.6l2-1.55-2-3.45-2.45 1a7.6 7.6 0 0 0-2.75-1.6L13.55 2h-4l-.4 2.8A7.6 7.6 0 0 0 6.4 6.4l-2.45-1-2 3.45 2 1.55A7.6 7.6 0 0 0 3.9 12a7.6 7.6 0 0 0 .05 1.6l-2 1.55 2 3.45 2.45-1a7.6 7.6 0 0 0 2.75 1.6l.4 2.8h4l.4-2.8a7.6 7.6 0 0 0 2.75-1.6l2.45 1 2-3.45-2-1.55Z" />
        </>
      )}
      {name === "users" && (
        <>
          <path {...common} d="M9 11a3.25 3.25 0 1 0 0-6.5A3.25 3.25 0 0 0 9 11Z" />
          <path {...common} d="M3.5 19.25a5.5 5.5 0 0 1 11 0" />
          <path {...common} d="M15.5 5.1a3.25 3.25 0 0 1 0 5.8M18 19.25a5.4 5.4 0 0 0-2.8-4.75" />
        </>
      )}
    </svg>
  );
}

function readInitialNavCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(NAV_COLLAPSED_STORAGE_KEY) === "1";
}

/** Per-parent expand state. Default: every parent expanded. User can collapse
 * via the chevron and the choice persists across reloads. */
function readInitialExpandedParents(): Record<string, boolean> {
  const defaults: Record<string, boolean> = {};
  for (const item of BASE_NAV_ITEMS) {
    if (item.children?.length) defaults[item.path] = true;
  }
  if (typeof window === "undefined") return defaults;
  try {
    const raw = window.localStorage.getItem(NAV_EXPANDED_PARENTS_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return { ...defaults, ...parsed };
    }
  } catch {
    /* ignore */
  }
  return defaults;
}

/** Chat 导航项的注意力徽标（未读 / 待审批 / 待澄清的会话数）。 */
function ChatNavBadge() {
  const runtime = useOptionalChatRuntime();
  const count = runtime?.chatAttentionCount || 0;
  if (count <= 0) return null;
  return (
    <span
      aria-label={`${count} sessions need attention`}
      className="nav-attention inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--aisoc-accent)] px-1 font-mono text-[9px] font-bold leading-none text-[var(--aisoc-on-accent)]"
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

export function AppShell() {
  const location = useLocation();
  const currentUser = useCurrentUser();
  const navItems = buildNavItems(Boolean(currentUser?.is_admin));
  const [navCollapsed, setNavCollapsed] = useState<boolean>(readInitialNavCollapsed);
  const [expandedParents, setExpandedParents] = useState<Record<string, boolean>>(readInitialExpandedParents);
  const [theme, setTheme] = useState<ThemeMode>(readTheme);

  function toggleParent(path: string): void {
    setExpandedParents((prev) => {
      const next = { ...prev, [path]: !prev[path] };
      if (typeof window !== "undefined") {
        try {
          window.localStorage.setItem(NAV_EXPANDED_PARENTS_KEY, JSON.stringify(next));
        } catch {
          /* ignore quota */
        }
      }
      return next;
    });
  }

  const activeItem =
    navItems.find((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`)) ??
    navItems[0];
  const showWorkbenchTopbar = activeItem.path !== "/overview";

  function signOut(): void {
    clearStoredAuth();
    window.location.href = "/login";
  }

  function toggleNav(): void {
    setNavCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(NAV_COLLAPSED_STORAGE_KEY, next ? "1" : "0");
      }
      return next;
    });
  }

  function toggleTheme(): void {
    const next: ThemeMode = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  }

  // 统一聊天运行时：/chat 主视图可见。
  const isChatVisible = location.pathname.startsWith("/chat");

  return (
    <AisocChatProvider isChatVisible={isChatVisible}>
      <div className={`app-shell ${navCollapsed ? "nav-collapsed" : ""}`.trim()}>
        <div className="app-ambient" aria-hidden="true">
          <HudGrid />
        </div>
        <aside data-testid="side-nav" className="side-nav side-nav-workbench">
          <header className="side-nav-header">
            <div className="brand-stack">
              <div className="brand-orb" aria-hidden="true">
                <BrandMark size={30} className="brand-orb-mark" />
              </div>
              <div className="brand-text">
                <h1>AISOC</h1>
              </div>
            </div>
            <button
              type="button"
              className="ghost-button side-nav-toggle"
              onClick={toggleNav}
              aria-label={navCollapsed ? "Expand navigation" : "Collapse navigation"}
              title={navCollapsed ? "Expand navigation" : "Collapse navigation"}
              aria-expanded={!navCollapsed}
            >
              {navCollapsed ? "▸" : "◂"}
            </button>
          </header>
          <div className="side-nav-groups">
            <section className="side-nav-group">
              <p className="side-nav-group-label">Workbench</p>
              <nav aria-label="Workbench navigation">
                {navItems.map((item) => {
                  const isActive = location.pathname.startsWith(item.path);
                  const hasChildren = !!item.children?.length;
                  const expanded = hasChildren ? !!expandedParents[item.path] : false;
                  const showChildren = hasChildren && expanded && !navCollapsed;

                  return (
                    <div key={item.path} className={`nav-entry${hasChildren ? " nav-entry-parent" : ""}`}>
                      <div className="nav-entry-row">
                        {hasChildren ? (
                          <button
                            type="button"
                            className={`nav-parent-toggle${isActive ? " active" : ""}${expanded ? " expanded" : ""}`}
                            onClick={() => toggleParent(item.path)}
                            aria-expanded={expanded}
                            aria-controls={`nav-children-${item.icon}`}
                            title={item.label}
                          >
                            <span className="nav-link-icon">
                              <NavIcon name={item.icon} />
                            </span>
                            <span className="nav-link-label">{item.label}</span>
                            {!navCollapsed ? (
                              <span className="nav-parent-caret" aria-hidden="true">
                                <svg viewBox="0 0 12 12" width="10" height="10" focusable="false">
                                  <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                                </svg>
                              </span>
                            ) : null}
                          </button>
                        ) : (
                          <Link
                            to={item.path}
                            className={isActive ? "active" : ""}
                            aria-current={isActive ? "page" : undefined}
                            title={item.label}
                          >
                            <span className="nav-link-icon">
                              <NavIcon name={item.icon} />
                            </span>
                            <span className="nav-link-label">{item.label}</span>
                            {item.path === "/chat" ? <ChatNavBadge /> : null}
                          </Link>
                        )}
                      </div>
                      {showChildren ? (
                        <div
                          id={`nav-children-${item.icon}`}
                          className="nav-children"
                          role="group"
                          aria-label={`${item.label} sub-views`}
                        >
                          {item.children!.map((child) => {
                            if (child.external) {
                              return (
                                <a
                                  key={child.path}
                                  href={child.path}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="nav-child"
                                  title={child.label}
                                >
                                  <span className="nav-child-dot" aria-hidden="true" />
                                  <span className="nav-child-label">
                                    <span className="nav-child-en">{child.label}</span>
                                  </span>
                                </a>
                              );
                            }
                            const childActive = location.pathname === child.path
                              || location.pathname.startsWith(`${child.path}/`);
                            return (
                              <Link
                                key={child.path}
                                to={child.path}
                                className={`nav-child${childActive ? " active" : ""}`}
                                aria-current={childActive ? "page" : undefined}
                                title={child.label}
                              >
                                <span className="nav-child-dot" aria-hidden="true" />
                                <span className="nav-child-label">
                                  <span className="nav-child-en">{child.label}</span>
                                </span>
                              </Link>
                            );
                          })}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </nav>
            </section>
          </div>
          <footer className="side-nav-footer">
            <button
              className="ghost-button side-nav-footer-button side-nav-theme-toggle"
              type="button"
              onClick={toggleTheme}
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-pressed={theme === "light"}
            >
              <span className="nav-link-icon" aria-hidden="true">
                {theme === "dark" ? (
                  <svg viewBox="0 0 24 24" className="nav-icon-svg">
                    <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.7" />
                    <path
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      d="M12 3v2M12 19v2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M3 12h2M19 12h2M5.6 18.4 7 17M17 7l1.4-1.4"
                    />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" className="nav-icon-svg">
                    <path
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M20 14.5A8 8 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5Z"
                    />
                  </svg>
                )}
              </span>
              <span className="nav-link-label">{theme === "dark" ? "Light Theme" : "Dark Theme"}</span>
            </button>
            <button
              className="ghost-button side-nav-footer-button"
              type="button"
              onClick={signOut}
              title="Sign Out"
            >
              <span className="nav-link-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="nav-icon-svg">
                  <path
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M10 5H6.5A2.5 2.5 0 0 0 4 7.5v9A2.5 2.5 0 0 0 6.5 19H10M14 8l4 4-4 4M9 12h9"
                  />
                </svg>
              </span>
              <span className="nav-link-label">Sign Out</span>
            </button>
          </footer>
        </aside>
        <main className="main-panel workbench-main">
          {showWorkbenchTopbar ? (
            <header className="workbench-topbar">
              <div className="workbench-topbar-copy">
                <div className="workbench-topbar-brandmark" aria-hidden="true">
                  <BrandMark size={24} className="workbench-topbar-mark" />
                </div>
                <div className="workbench-topbar-copy-text">
                  <p className="brand-kicker">AISOC Workbench</p>
                  <h2>{activeItem.label}</h2>
                </div>
              </div>
              <div className="workbench-topbar-actions">
                <span className="status-badge status-live">Live</span>
                <button
                  className="ghost-button workbench-topbar-signout"
                  type="button"
                  onClick={signOut}
                  title="Sign Out"
                >
                  Sign Out
                </button>
              </div>
            </header>
          ) : null}
          <Outlet />
        </main>
      </div>
      {/* 悬浮聊天入口渲染在 grid 布局外层,避免被 <main>/外层容器的
          overflow-hidden 裁掉,保证真正贴在浏览器视口右下角。 */}
      <FloatingChatWidget />
    </AisocChatProvider>
  );
}
