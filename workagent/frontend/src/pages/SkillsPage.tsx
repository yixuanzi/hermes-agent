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

type SkillGroup = {
  category: string;
  items: SkillRow[];
  disabledCount: number;
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

function getApiErrorDetail(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  try {
    const payload = JSON.parse(error.message) as { detail?: unknown };
    return typeof payload.detail === "string" && payload.detail ? payload.detail : fallback;
  } catch {
    return fallback;
  }
}

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedSkillName, setSelectedSkillName] = useState("");

  const [pendingSkill, setPendingSkill] = useState("");
  const [pendingDeleteSkill, setPendingDeleteSkill] = useState("");
  const [pendingCategory, setPendingCategory] = useState("");
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

  const [isEditingContent, setIsEditingContent] = useState(false);
  const [contentDraft, setContentDraft] = useState("");
  const [contentSaving, setContentSaving] = useState(false);
  const [contentSaveError, setContentSaveError] = useState("");
  const [contentSaveSuccess, setContentSaveSuccess] = useState("");

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

  function resetContentEditing() {
    setIsEditingContent(false);
    setContentDraft("");
    setContentSaving(false);
    setContentSaveError("");
    setContentSaveSuccess("");
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
    resetContentEditing();
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
    resetContentEditing();
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

  async function toggleCategory(category: string, enabled: boolean) {
    setPendingCategory(category);
    setActionError("");
    setActionSuccess("");
    try {
      await fetchJSON("/api/skills/toggle-category", {
        method: "PUT",
        body: JSON.stringify({ category, enabled }),
      });
      const refreshSucceeded = await loadSkills();
      if (!refreshSucceeded) {
        setActionError(`Updated ${category}, but failed to refresh skills from /api/skills.`);
        return;
      }
      setActionSuccess(`${category} ${enabled ? "enabled" : "disabled"} successfully.`);
    } catch (error) {
      setActionError(getApiErrorDetail(error, `Failed to ${enabled ? "enable" : "disable"} ${category}.`));
    } finally {
      setPendingCategory("");
    }
  }

  async function deleteSkill(name: string) {
    if (!name) return;
    const confirmed = window.confirm(
      `Delete skill "${name}" and everything in its folder? This cannot be undone.`,
    );
    if (!confirmed) return;

    setPendingDeleteSkill(name);
    setActionError("");
    setActionSuccess("");
    try {
      await fetchJSON(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
      setDetail(null);
      setSelectedAppendixPath("");
      setAppendixContent(null);
      const refreshSucceeded = await loadSkills();
      if (!refreshSucceeded) {
        setSkills((current) => current.filter((skill) => skill.name !== name));
        setSelectedSkillName((current) => (current === name ? "" : current));
        setActionError(`Deleted ${name}, but failed to refresh skills from /api/skills.`);
        return;
      }
      setActionSuccess(`${name} deleted successfully.`);
    } catch (error) {
      setActionError(getApiErrorDetail(error, `Failed to delete ${name}.`));
    } finally {
      setPendingDeleteSkill("");
    }
  }

  function confirmDiscardEditIfNeeded(): boolean {
    if (!isEditingContent) return true;
    const editingAppendixNow = Boolean(selectedAppendixPath) && appendixContent?.path === selectedAppendixPath;
    const original = editingAppendixNow ? appendixContent?.content ?? "" : detail?.content ?? "";
    if (contentDraft === original) {
      resetContentEditing();
      return true;
    }
    const confirmed = window.confirm("You have unsaved edits. Discard changes?");
    if (confirmed) resetContentEditing();
    return confirmed;
  }

  function startEditingContent() {
    if (!detail) return;
    const editingAppendixNow = Boolean(selectedAppendixPath) && appendixContent?.path === selectedAppendixPath;
    setContentDraft(editingAppendixNow ? appendixContent?.content ?? "" : detail.content);
    setIsEditingContent(true);
    setContentSaveError("");
    setContentSaveSuccess("");
  }

  function cancelEditingContent() {
    setIsEditingContent(false);
    setContentDraft("");
    setContentSaveError("");
  }

  async function saveEditedContent() {
    if (!detail || contentSaving) return;
    const editingAppendixNow = Boolean(selectedAppendixPath) && appendixContent?.path === selectedAppendixPath;
    setContentSaving(true);
    setContentSaveError("");
    setContentSaveSuccess("");
    try {
      if (editingAppendixNow && appendixContent) {
        await fetchJSON(`/api/skills/${encodeURIComponent(detail.name)}/appendix`, {
          method: "PUT",
          body: JSON.stringify({ path: appendixContent.path, content: contentDraft }),
        });
        setAppendixContent({ ...appendixContent, content: contentDraft });
        setContentSaveSuccess(`${appendixContent.path} saved.`);
      } else {
        await fetchJSON(`/api/skills/${encodeURIComponent(detail.name)}`, {
          method: "PUT",
          body: JSON.stringify({ content: contentDraft }),
        });
        setDetail({ ...detail, content: contentDraft });
        setContentSaveSuccess("SKILL.md saved.");
      }
      setIsEditingContent(false);
    } catch (error) {
      setContentSaveError(getApiErrorDetail(error, "Failed to save content."));
    } finally {
      setContentSaving(false);
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

  const groupedSkills = useMemo<SkillGroup[]>(() => {
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
        disabledCount: items.filter((skill) => !skill.enabled).length,
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
  const disabledSkillCount = filteredSkills.filter((skill) => !skill.enabled).length;
  const mutationInFlight = Boolean(pendingSkill) || Boolean(pendingDeleteSkill) || Boolean(pendingCategory);
  const editingAppendix =
    isEditingContent && Boolean(selectedAppendixPath) && appendixContent?.path === selectedAppendixPath;
  const contentEditOriginal = editingAppendix ? appendixContent?.content ?? "" : detail?.content ?? "";
  const hasContentUnsavedChanges = contentDraft !== contentEditOriginal;

  return (
    <section className="skills-workbench-page">
      {error ? <StateBlock kind="error" title="Skill Inventory Unavailable" message={error} /> : null}
      <div className="skills-workbench skills-layout-revamp">
        <article className="detail-panel skills-list-pane">
          <div className="skills-list-head">
            <h3>Skills</h3>
            <div className="skills-counts" aria-label="Skill counts">
              <span className="status-badge">
                {isSearching ? `${filteredSkills.length} / ${skills.length}` : `${skills.length} total`}
              </span>
              <span className="skills-disabled-count">{disabledSkillCount} disabled</span>
            </div>
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
                <div className="skills-category-head">
                  <button
                    type="button"
                    className="skills-category-toggle"
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
                      <span className="skills-disabled-count">{group.disabledCount} disabled</span>
                      <span className="skills-category-chevron" aria-hidden="true">
                        {isSearching || !collapsedCategories[group.category] ? "▾" : "▸"}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="ghost-button skills-category-bulk-toggle"
                    disabled={mutationInFlight}
                    onClick={() => void toggleCategory(group.category, group.disabledCount > 0)}
                    title={
                      group.disabledCount > 0
                        ? `Enable all skills in ${group.category}`
                        : `Disable all skills in ${group.category}`
                    }
                  >
                    {pendingCategory === group.category
                      ? "..."
                      : group.disabledCount > 0
                        ? "Enable all"
                        : "Disable all"}
                  </button>
                </div>
                {isSearching || !collapsedCategories[group.category] ? (
                  <ul className="list-grid skills-narrow-list">
                    {group.items.map((skill) => (
                      <li
                        key={skill.name}
                        className={selectedSkillName === skill.name ? "clickable-card active" : "clickable-card"}
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                          if (!confirmDiscardEditIfNeeded()) return;
                          setSelectedSkillName(skill.name);
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ") return;
                          event.preventDefault();
                          if (!confirmDiscardEditIfNeeded()) return;
                          setSelectedSkillName(skill.name);
                        }}
                      >
                        <div className="skills-narrow-item-top">
                          <strong>{skill.name}</strong>
                          <button
                            type="button"
                            className="ghost-button skills-mini-toggle"
                            disabled={mutationInFlight}
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
              <div className="skills-detail-head-actions">
                <span className="status-badge">{selectedSkill.enabled ? "Enabled" : "Disabled"}</span>
                {!isEditingContent ? (
                  <button
                    type="button"
                    className="ghost-button skills-edit-button"
                    disabled={!detail || detailLoading}
                    onClick={startEditingContent}
                    title="Edit content"
                  >
                    Edit
                  </button>
                ) : !editingAppendix ? (
                  <>
                    <button
                      type="button"
                      className="ghost-button skills-cancel-edit-button"
                      disabled={contentSaving}
                      onClick={cancelEditingContent}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="wiki-save-button"
                      disabled={contentSaving || !hasContentUnsavedChanges}
                      onClick={() => void saveEditedContent()}
                    >
                      {contentSaving ? "Saving..." : "Save"}
                    </button>
                  </>
                ) : null}
                <button
                  type="button"
                  className="ghost-button danger-button skills-delete-button"
                  disabled={Boolean(pendingSkill || pendingDeleteSkill) || isEditingContent}
                  onClick={() => void deleteSkill(selectedSkill.name)}
                  aria-label={`Delete skill ${selectedSkill.name}`}
                  title="Delete skill"
                >
                  {pendingDeleteSkill === selectedSkill.name ? "Deleting..." : "Delete"}
                </button>
              </div>
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
                      {editingAppendix ? (
                        <div className="skills-content-toolbar-actions">
                          <button
                            type="button"
                            className="ghost-button skills-cancel-edit-button"
                            disabled={contentSaving}
                            onClick={cancelEditingContent}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="wiki-save-button"
                            disabled={contentSaving || !hasContentUnsavedChanges}
                            onClick={() => void saveEditedContent()}
                          >
                            {contentSaving ? "Saving..." : "Save"}
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="ghost-button skills-back-to-skill"
                          onClick={() => {
                            if (!confirmDiscardEditIfNeeded()) return;
                            setSelectedAppendixPath("");
                            setAppendixError("");
                          }}
                        >
                          Back to SKILL.md
                        </button>
                      )}
                    </div>
                    {editingAppendix ? (
                      <textarea
                        className="skills-content-editor"
                        aria-label={`Appendix source: ${appendixContent.path}`}
                        value={contentDraft}
                        disabled={contentSaving}
                        onChange={(event) => {
                          setContentDraft(event.target.value);
                          setContentSaveError("");
                          setContentSaveSuccess("");
                        }}
                        spellCheck={false}
                      />
                    ) : (
                      <pre>{appendixContent.content}</pre>
                    )}
                  </div>
                ) : (
                  <div className="skills-markdown-view">
                    {isEditingContent && !editingAppendix ? (
                      <textarea
                        className="skills-content-editor"
                        aria-label="SKILL.md source"
                        value={contentDraft}
                        disabled={contentSaving}
                        onChange={(event) => {
                          setContentDraft(event.target.value);
                          setContentSaveError("");
                          setContentSaveSuccess("");
                        }}
                        spellCheck={false}
                      />
                    ) : (
                      <pre>{detail.content}</pre>
                    )}
                  </div>
                )}
                {contentSaveError ? <p className="error-text">{contentSaveError}</p> : null}
                {contentSaveSuccess ? <p className="wiki-save-success">{contentSaveSuccess}</p> : null}
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
                      onClick={() => {
                        if (!confirmDiscardEditIfNeeded()) return;
                        void loadAppendixContent(detail.name, appendix.path);
                      }}
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
          {pendingCategory ? (
            <StateBlock
              kind="loading"
              title="Applying Category Toggle"
              message={`Updating ${pendingCategory} via /api/skills/toggle-category.`}
            />
          ) : null}
          {pendingDeleteSkill ? (
            <StateBlock
              kind="loading"
              title="Deleting Skill"
              message={`Deleting ${pendingDeleteSkill} and its folder.`}
            />
          ) : null}
          {actionSuccess ? <StateBlock kind="success" title="Operation Completed" message={actionSuccess} /> : null}
          {actionError ? <StateBlock kind="error" title="Operation Failed" message={actionError} /> : null}
        </aside>
      </div>
    </section>
  );
}
