import { useEffect, useMemo, useState } from "react";

import { StateBlock } from "../components/StateBlock";
import { fetchJSON } from "../lib/api";

type SkillRow = {
  name: string;
  description?: string;
  category?: string;
  enabled: boolean;
  path?: string;
};

type SkillAppendixItem = {
  name: string;
  path: string;
};

type SkillDetail = {
  name: string;
  path: string;
  content: string;
  appendix: SkillAppendixItem[];
};

type SkillAppendixContent = {
  name: string;
  path: string;
  content: string;
};

function normalizeCategory(category: string | undefined): string {
  const cleaned = (category || "").trim();
  return cleaned.length > 0 ? cleaned : "misc";
}

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedSkillName, setSelectedSkillName] = useState("");

  const [pendingSkill, setPendingSkill] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionSuccess, setActionSuccess] = useState("");

  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [selectedAppendixPath, setSelectedAppendixPath] = useState("");
  const [appendixContent, setAppendixContent] = useState<SkillAppendixContent | null>(null);
  const [appendixLoading, setAppendixLoading] = useState(false);
  const [appendixError, setAppendixError] = useState("");
  const [appendixCollapsed, setAppendixCollapsed] = useState(false);
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState("");

  async function loadSkills(): Promise<boolean> {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchJSON<SkillRow[]>("/api/skills");
      setSkills(payload || []);
      return true;
    } catch {
      setError("Failed to load skills.");
      return false;
    } finally {
      setLoading(false);
    }
  }

  async function loadSkillDetail(skillName: string) {
    if (!skillName) return;
    setDetailLoading(true);
    setDetailError("");
    setDetail(null);
    setSelectedAppendixPath("");
    setAppendixCollapsed(false);
    setAppendixContent(null);
    setAppendixError("");
    try {
      const payload = await fetchJSON<SkillDetail>(`/api/skills/${encodeURIComponent(skillName)}`);
      setDetail(payload);
    } catch {
      setDetailError("Failed to load skill detail.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function loadAppendixContent(skillName: string, appendixPath: string) {
    if (!skillName || !appendixPath) return;
    setAppendixLoading(true);
    setAppendixError("");
    setSelectedAppendixPath(appendixPath);
    try {
      const encodedPath = encodeURIComponent(appendixPath);
      const payload = await fetchJSON<SkillAppendixContent>(
        `/api/skills/${encodeURIComponent(skillName)}/appendix?path=${encodedPath}`,
      );
      setAppendixContent(payload);
    } catch {
      setAppendixError("Failed to load appendix content.");
    } finally {
      setAppendixLoading(false);
    }
  }

  async function toggleSkill(name: string, enabled: boolean) {
    setPendingSkill(name);
    setActionError("");
    setActionSuccess("");
    try {
      await fetchJSON("/api/skills/toggle", {
        method: "PUT",
        body: JSON.stringify({ name, enabled }),
      });
      const refreshSucceeded = await loadSkills();
      if (!refreshSucceeded) {
        setActionError(`Updated ${name}, but failed to refresh skills from /api/skills.`);
        return;
      }
      setActionSuccess(`${name} ${enabled ? "enabled" : "disabled"} successfully.`);
    } catch {
      setActionError(`Failed to ${enabled ? "enable" : "disable"} ${name}.`);
    } finally {
      setPendingSkill("");
    }
  }

  useEffect(() => {
    void loadSkills();
  }, []);

  useEffect(() => {
    if (skills.length === 0) {
      setSelectedSkillName("");
      setDetail(null);
      return;
    }
    const exists = skills.some((skill) => skill.name === selectedSkillName);
    const target = exists ? selectedSkillName : skills[0].name;
    if (target !== selectedSkillName) {
      setSelectedSkillName(target);
      return;
    }
    void loadSkillDetail(target);
  }, [skills, selectedSkillName]);

  const normalizedQuery = search.trim().toLowerCase();
  const isSearching = normalizedQuery.length > 0;

  const filteredSkills = useMemo(() => {
    if (!normalizedQuery) return skills;
    return skills.filter((skill) => {
      const haystack = `${skill.name} ${skill.description || ""} ${normalizeCategory(skill.category)}`.toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [skills, normalizedQuery]);

  const groupedSkills = useMemo(() => {
    const groups = new Map<string, SkillRow[]>();
    for (const skill of filteredSkills) {
      const category = normalizeCategory(skill.category);
      const bucket = groups.get(category) || [];
      bucket.push(skill);
      groups.set(category, bucket);
    }
    return Array.from(groups.entries())
      .map(([category, items]) => ({
        category,
        items: items.sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => a.category.localeCompare(b.category));
  }, [filteredSkills]);

  useEffect(() => {
    setCollapsedCategories((current) => {
      const next: Record<string, boolean> = {};
      for (const group of groupedSkills) {
        next[group.category] = Object.prototype.hasOwnProperty.call(current, group.category)
          ? current[group.category]
          : true;
      }
      return next;
    });
  }, [groupedSkills]);

  const selectedSkill = skills.find((skill) => skill.name === selectedSkillName) || null;

  return (
    <section className="skills-workbench-page">
      {error ? <StateBlock kind="error" title="Skill Inventory Unavailable" message={error} /> : null}
      <div className="skills-workbench skills-layout-revamp">
        <article className="detail-panel skills-list-pane">
          <div className="skills-list-head">
            <h3>Skills</h3>
            <span className="status-badge">
              {isSearching ? `${filteredSkills.length} / ${skills.length}` : `${skills.length} total`}
            </span>
          </div>
          <div className="skills-search">
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search skills by name, description, or category..."
              aria-label="Search skills"
            />
            {isSearching ? (
              <button
                type="button"
                className="ghost-button skills-search-clear"
                onClick={() => setSearch("")}
                aria-label="Clear skill search"
              >
                Clear
              </button>
            ) : null}
          </div>
          <p className="subtle-copy">Categorized skill index. Empty category is grouped under misc.</p>
          {loading ? (
            <StateBlock kind="loading" title="Loading Skills" message="Fetching inventory from /api/skills." />
          ) : null}
          {!loading && !error && skills.length === 0 ? (
            <StateBlock kind="empty" title="No Skills Found" message="No skill entries were returned for this profile." />
          ) : null}
          {!loading && !error && skills.length > 0 && isSearching && filteredSkills.length === 0 ? (
            <StateBlock kind="empty" title="No Matches" message={`No skills match "${search.trim()}".`} />
          ) : null}
          <div className="skills-list-scroll">
            {groupedSkills.map((group) => (
              <section key={group.category} className="skills-category-block">
                <button
                  type="button"
                  className="skills-category-head skills-category-toggle"
                  onClick={() =>
                    setCollapsedCategories((current) => ({
                      ...current,
                      [group.category]: !current[group.category],
                    }))
                  }
                  aria-label={`Toggle ${group.category} category`}
                  aria-expanded={isSearching || !collapsedCategories[group.category]}
                >
                  <h4>{group.category}</h4>
                  <span className="skills-category-head-right">
                    <span className="status-badge">{group.items.length}</span>
                    <span className="skills-category-chevron" aria-hidden="true">
                      {isSearching || !collapsedCategories[group.category] ? "▾" : "▸"}
                    </span>
                  </span>
                </button>
                {isSearching || !collapsedCategories[group.category] ? (
                  <ul className="list-grid skills-narrow-list">
                    {group.items.map((skill) => (
                      <li
                        key={skill.name}
                        className={selectedSkillName === skill.name ? "clickable-card active" : "clickable-card"}
                        role="button"
                        tabIndex={0}
                        onClick={() => setSelectedSkillName(skill.name)}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ") return;
                          event.preventDefault();
                          setSelectedSkillName(skill.name);
                        }}
                      >
                        <div className="skills-narrow-item-top">
                          <strong>{skill.name}</strong>
                          <button
                            type="button"
                            className="ghost-button skills-mini-toggle"
                            disabled={pendingSkill === skill.name}
                            onClick={(event) => {
                              event.stopPropagation();
                              void toggleSkill(skill.name, !skill.enabled);
                            }}
                            title={skill.enabled ? "Disable skill" : "Enable skill"}
                          >
                            {pendingSkill === skill.name ? "..." : skill.enabled ? "On" : "Off"}
                          </button>
                        </div>
                        <p>{skill.description || "No description available."}</p>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ))}
          </div>
        </article>

        <aside className="detail-panel skills-detail-pane">
          <div className="skills-detail-head">
            <h3>{selectedSkillName || "Skill Detail"}</h3>
            {selectedSkill ? (
              <span className="status-badge">{selectedSkill.enabled ? "Enabled" : "Disabled"}</span>
            ) : null}
          </div>
          {selectedSkill?.path ? (
            <p className="subtle-copy">{`Path: ${selectedSkill.path}`}</p>
          ) : null}
          {detailLoading ? <StateBlock kind="loading" title="Loading Detail" message="Fetching /api/skills/{skill_name}." /> : null}
          {detailError ? <StateBlock kind="error" title="Detail Unavailable" message={detailError} /> : null}

          <div className="skills-detail-main">
            {!detailLoading && !detailError && detail ? (
              <>
                {selectedAppendixPath && appendixContent?.path === selectedAppendixPath ? (
                  <div className="skills-appendix-content">
                    <div className="skills-content-toolbar">
                      <p>
                        <strong>{`Appendix: ${appendixContent.path}`}</strong>
                      </p>
                      <button
                        type="button"
                        className="ghost-button skills-back-to-skill"
                        onClick={() => {
                          setSelectedAppendixPath("");
                          setAppendixError("");
                        }}
                      >
                        Back to SKILL.md
                      </button>
                    </div>
                    <pre>{appendixContent.content}</pre>
                  </div>
                ) : (
                  <div className="skills-markdown-view">
                    <pre>{detail.content}</pre>
                  </div>
                )}
                {appendixLoading ? (
                  <p className="subtle-copy">Loading appendix content...</p>
                ) : null}
                {appendixError ? <p className="error-text">{appendixError}</p> : null}
              </>
            ) : null}
          </div>

          <div className="skills-appendix-rail">
            <button
              type="button"
              className="skills-appendix-toggle"
              onClick={() => setAppendixCollapsed((current) => !current)}
              aria-label="Toggle appendix files panel"
              aria-expanded={!appendixCollapsed}
            >
              <span className="skills-appendix-title">Appendix Files</span>
              <span className="skills-appendix-toggle-icon" aria-hidden="true">
                {appendixCollapsed ? "▸" : "▾"}
              </span>
            </button>
            {!appendixCollapsed ? (
              !detail || detail.appendix.length === 0 ? (
                <p className="subtle-copy">No appendix files.</p>
              ) : (
                <div className="skills-appendix-paths">
                  {detail.appendix.map((appendix) => (
                    <button
                      key={appendix.path}
                      type="button"
                      className={
                        selectedAppendixPath === appendix.path
                          ? "ghost-button skills-appendix-chip active"
                          : "ghost-button skills-appendix-chip"
                      }
                      onClick={() => void loadAppendixContent(detail.name, appendix.path)}
                    >
                      {appendix.path}
                    </button>
                  ))}
                </div>
              )
            ) : null}
          </div>

          {pendingSkill ? (
            <StateBlock
              kind="loading"
              title="Applying Toggle"
              message={`Updating ${pendingSkill} via /api/skills/toggle.`}
            />
          ) : null}
          {actionSuccess ? <StateBlock kind="success" title="Operation Completed" message={actionSuccess} /> : null}
          {actionError ? <StateBlock kind="error" title="Operation Failed" message={actionError} /> : null}
        </aside>
      </div>
    </section>
  );
}
