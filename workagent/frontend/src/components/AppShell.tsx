import { Link, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";

import { clearStoredToken } from "../lib/auth";
import { FloatingChat } from "./FloatingChat";

type NavIconName = "overview" | "chat" | "sessions" | "cron" | "skills" | "wiki" | "memory" | "ontology" | "settings";

interface NavChild {
  path: string;
  label: string;
  zh?: string;
}

interface NavEntry {
  path: string;
  label: string;
  icon: NavIconName;
  children?: NavChild[];
}

const NAV_ITEMS: NavEntry[] = [
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
      { path: "/ontology/standard-graph", label: "Standard Graph", zh: "标准图谱" },
      { path: "/ontology/diff-overview", label: "Diff Overview", zh: "差异总览" },
      { path: "/ontology/chat", label: "Ontology Chat", zh: "本体对话" },
    ],
  },
  { path: "/settings", label: "Settings", icon: "settings" },
];

const NAV_COLLAPSED_STORAGE_KEY = "workagent_nav_collapsed";
const NAV_EXPANDED_PARENTS_KEY = "workagent_nav_expanded_parents";
const BRAND_LOGO_SRC = `${import.meta.env.BASE_URL}workagent-logo.svg?v=2`;

function readInitialNavCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(NAV_COLLAPSED_STORAGE_KEY) === "1";
}

/** Per-parent expand state. Default: every parent expanded. User can collapse
 * via the chevron and the choice persists across reloads. */
function readInitialExpandedParents(): Record<string, boolean> {
  const defaults: Record<string, boolean> = {};
  for (const item of NAV_ITEMS) {
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
      {name === "settings" && (
        <>
          <path {...common} d="M12 15.25A3.25 3.25 0 1 0 12 8.75a3.25 3.25 0 0 0 0 6.5Z" />
          <path {...common} d="M19.1 13.6a7.6 7.6 0 0 0 .05-1.6 7.6 7.6 0 0 0-.05-1.6l2-1.55-2-3.45-2.45 1a7.6 7.6 0 0 0-2.75-1.6L13.55 2h-4l-.4 2.8A7.6 7.6 0 0 0 6.4 6.4l-2.45-1-2 3.45 2 1.55A7.6 7.6 0 0 0 3.9 12a7.6 7.6 0 0 0 .05 1.6l-2 1.55 2 3.45 2.45-1a7.6 7.6 0 0 0 2.75 1.6l.4 2.8h4l.4-2.8a7.6 7.6 0 0 0 2.75-1.6l2.45 1 2-3.45-2-1.55Z" />
        </>
      )}
    </svg>
  );
}

export function AppShell() {
  const location = useLocation();
  const [navCollapsed, setNavCollapsed] = useState<boolean>(readInitialNavCollapsed);
  const [expandedParents, setExpandedParents] = useState<Record<string, boolean>>(readInitialExpandedParents);

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
    NAV_ITEMS.find((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`)) ??
    NAV_ITEMS[0];
  const showWorkbenchTopbar = activeItem.path !== "/overview";

  function signOut(): void {
    clearStoredToken();
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

  return (
    <div className={`app-shell ${navCollapsed ? "nav-collapsed" : ""}`.trim()}>
      <aside className="side-nav side-nav-workbench">
        <header className="side-nav-header">
          <div className="brand-stack">
            <div className="brand-orb" aria-hidden="true">
              <img src={BRAND_LOGO_SRC} alt="" className="brand-orb-logo" />
            </div>
            <div className="brand-text">
              <p className="brand-kicker">Hermes</p>
              <h1>WORKAGENT</h1>
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
              {NAV_ITEMS.map((item) => {
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
                          const childActive = location.pathname === child.path
                            || location.pathname.startsWith(`${child.path}/`);
                          return (
                            <Link
                              key={child.path}
                              to={child.path}
                              className={`nav-child${childActive ? " active" : ""}`}
                              aria-current={childActive ? "page" : undefined}
                              title={child.zh ? `${child.label} · ${child.zh}` : child.label}
                            >
                              <span className="nav-child-dot" aria-hidden="true" />
                              <span className="nav-child-label">
                                <span className="nav-child-en">{child.label}</span>
                                {child.zh ? <span className="nav-child-zh">{child.zh}</span> : null}
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
                <img src={BRAND_LOGO_SRC} alt="" className="workbench-topbar-logo" />
              </div>
              <div className="workbench-topbar-copy-text">
                <p className="brand-kicker">WORKAGENT Workbench</p>
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
        <FloatingChat />
      </main>
    </div>
  );
}
