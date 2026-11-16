import { useEffect, useState } from "react";

import { PageMissionHeader } from "../components/PageMissionHeader";
import { StateBlock } from "../components/StateBlock";
import { fetchJSON } from "../lib/api";

type HubSkill = {
  name: string;
  description: string;
  installed: boolean;
  in_catalog: boolean;
  installed_at: string | null;
  path: string | null;
};

type CatalogResponse = {
  skills: HubSkill[];
  catalog_available: boolean;
  catalog_error: string | null;
};

type HubStatus = {
  url: string;
  configured: boolean;
  api_key_configured: boolean;
};

function extractApiDetail(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) {
    try {
      const parsed = JSON.parse(err.message) as { detail?: string };
      if (parsed && typeof parsed.detail === "string" && parsed.detail) return parsed.detail;
    } catch {
      return err.message;
    }
    return err.message;
  }
  return fallback;
}

export function SkillHubPage() {
  const [skills, setSkills] = useState<HubSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [catalogAvailable, setCatalogAvailable] = useState(true);
  const [catalogError, setCatalogError] = useState("");

  const [hubStatus, setHubStatus] = useState<HubStatus | null>(null);

  const [actionPending, setActionPending] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionSuccess, setActionSuccess] = useState("");

  const [helpOpen, setHelpOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);
  const [installIdentifier, setInstallIdentifier] = useState("");
  const [installPending, setInstallPending] = useState(false);
  const [installError, setInstallError] = useState("");

  async function loadSkills() {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchJSON<CatalogResponse>("/api/skill-hub/catalog");
      setSkills(payload.skills || []);
      setCatalogAvailable(payload.catalog_available);
      setCatalogError(payload.catalog_error || "");
    } catch {
      setError("加载 SkillHub 目录失败。");
    } finally {
      setLoading(false);
    }
  }

  async function loadHubStatus() {
    try {
      setHubStatus(await fetchJSON<HubStatus>("/api/skill-hub/status"));
    } catch {
      setHubStatus(null);
    }
  }

  useEffect(() => {
    void loadSkills();
    void loadHubStatus();
  }, []);

  useEffect(() => {
    if (!installOpen) return;
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setInstallOpen(false);
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [installOpen]);

  async function installSkill() {
    const identifier = installIdentifier.trim();
    if (!identifier || installPending) return;
    setInstallPending(true);
    setInstallError("");
    try {
      await fetchJSON("/api/skill-hub/skills", {
        method: "POST",
        body: JSON.stringify({ identifier }),
      });
      setInstallOpen(false);
      setInstallIdentifier("");
      setActionSuccess(`已从 SkillHub 安装 ${identifier}。`);
      setActionError("");
      await loadSkills();
    } catch (err) {
      setInstallError(extractApiDetail(err, `安装 ${identifier} 失败。`));
    } finally {
      setInstallPending(false);
    }
  }

  async function installFromCatalog(name: string) {
    if (actionPending) return;
    setActionPending(`${name}:install`);
    setActionError("");
    setActionSuccess("");
    try {
      await fetchJSON("/api/skill-hub/skills", {
        method: "POST",
        body: JSON.stringify({ identifier: name }),
      });
      setActionSuccess(`已从 SkillHub 安装 ${name}。`);
      await loadSkills();
    } catch (err) {
      setActionError(extractApiDetail(err, `安装 ${name} 失败。`));
    } finally {
      setActionPending("");
    }
  }

  async function syncSkill(name: string) {
    if (actionPending) return;
    setActionPending(`${name}:sync`);
    setActionError("");
    setActionSuccess("");
    try {
      await fetchJSON(`/api/skill-hub/skills/${encodeURIComponent(name)}/sync`, { method: "POST" });
      setActionSuccess(`已从 SkillHub 同步 ${name} 的最新版本。`);
      await loadSkills();
    } catch (err) {
      setActionError(extractApiDetail(err, `同步 ${name} 失败。`));
    } finally {
      setActionPending("");
    }
  }

  async function uninstallSkill(name: string) {
    if (actionPending) return;
    if (!window.confirm(`卸载 ${name}？将删除本地 skill 目录并移除安装记录，此操作不可撤销。`)) return;
    setActionPending(`${name}:uninstall`);
    setActionError("");
    setActionSuccess("");
    try {
      await fetchJSON(`/api/skill-hub/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
      setActionSuccess(`已卸载 ${name}。`);
      await loadSkills();
    } catch (err) {
      setActionError(extractApiDetail(err, `卸载 ${name} 失败。`));
    } finally {
      setActionPending("");
    }
  }

  return (
    <section className="skills-workbench-page">
      <PageMissionHeader
        title="SkillHub 集成"
        subtitle="展示 SkillHub 目录中的全部 Skill 及其本地安装状态：本页只做状态同步与安装/卸载触发，内容与版本维护在 SkillHub 侧完成。"
        status={<span className="status-badge">{skills.length} 个</span>}
        actions={
          <>
            <button type="button" className="ghost-button" onClick={() => setHelpOpen(true)}>
              帮助
            </button>
            {hubStatus?.url ? (
              <a className="ghost-button" href={hubStatus.url} target="_blank" rel="noreferrer">
                打开 SkillHub ↗
              </a>
            ) : null}
            <button
              type="button"
              onClick={() => {
                setInstallError("");
                setInstallOpen(true);
              }}
            >
              + 安装
            </button>
          </>
        }
      />

      {error ? <StateBlock kind="error" title="加载失败" message={error} /> : null}
      {!error && !catalogAvailable ? (
        <StateBlock
          kind="error"
          title="无法获取 SkillHub 远程目录"
          message={`${catalogError || "未知错误"}。下方仍展示本地已安装的 Skill。`}
        />
      ) : null}
      {actionSuccess ? <p>{actionSuccess}</p> : null}
      {actionError ? <p className="error-text">{actionError}</p> : null}

      {loading ? <StateBlock kind="loading" title="加载中" message="正在获取 /api/skill-hub/catalog。" /> : null}
      {!loading && !error && skills.length === 0 ? (
        <StateBlock kind="empty" title="SkillHub 目录为空" message='点击右上角「+ 安装」，输入在 SkillHub 中创建好的 Skill 名称即可集成。' />
      ) : null}

      {!loading && skills.length > 0 ? (
        <ul className="list-grid cron-jobs-grid">
          {skills.map((skill) => (
            <li key={skill.name} className="cron-job-card">
              <div className="cron-job-card-head">
                <strong>{skill.name}</strong>
                {!skill.in_catalog ? (
                  <span className="status-badge" title="未在 SkillHub 目录中找到，可能不是通过本页集成的">
                    本地未知来源
                  </span>
                ) : skill.installed ? (
                  <span className="status-badge status-live">已安装</span>
                ) : (
                  <span className="status-badge">可安装</span>
                )}
              </div>
              <p className="subtle-copy min-w-0 break-all">{skill.description || "SkillHub 未提供描述。"}</p>
              {skill.installed_at ? (
                <p className="subtle-copy min-w-0 break-all">安装时间：{new Date(skill.installed_at).toLocaleString()}</p>
              ) : null}
              <div className="cron-card-actions flex-wrap">
                {skill.installed ? (
                  <>
                    <button
                      type="button"
                      className="ghost-button cron-icon-button cron-icon-button-detail"
                      disabled={actionPending !== "" || !skill.in_catalog}
                      onClick={() => void syncSkill(skill.name)}
                      title={
                        skill.in_catalog
                          ? "从 SkillHub 拉取最新版本覆盖本地"
                          : "该 Skill 未在 SkillHub 目录中找到，无法确认同步来源"
                      }
                    >
                      {actionPending === `${skill.name}:sync` ? "同步中…" : "同步"}
                    </button>
                    <button
                      type="button"
                      className="ghost-button cron-icon-button cron-icon-button-detail"
                      disabled={actionPending !== ""}
                      onClick={() => void uninstallSkill(skill.name)}
                      title="删除本地 skill 目录并移除安装记录"
                    >
                      {actionPending === `${skill.name}:uninstall` ? "卸载中…" : "卸载"}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="ghost-button cron-icon-button cron-icon-button-detail"
                    disabled={actionPending !== ""}
                    onClick={() => void installFromCatalog(skill.name)}
                    title="从 SkillHub 安装该 Skill"
                  >
                    {actionPending === `${skill.name}:install` ? "安装中…" : "安装"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <div
        className={installOpen ? "cron-create-modal-overlay active" : "cron-create-modal-overlay"}
        onClick={() => {
          if (!installPending) setInstallOpen(false);
        }}
        aria-hidden={!installOpen}
      >
        <div
          className="cron-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-label="从 SkillHub 安装 Skill"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="cron-detail-modal-header">
            <h4>从 SkillHub 安装 Skill</h4>
            <button
              type="button"
              className="ghost-button cron-raw-modal-close"
              onClick={() => setInstallOpen(false)}
              disabled={installPending}
              aria-label="关闭"
            >
              ×
            </button>
          </div>
          <div className="cron-detail-modal-body">
            <p className="subtle-copy">
              输入你在 SkillHub 中已创建好的 Skill 名称。前置步骤（注册资源、托管 token、授权、生成 Skill）
              请先在 SkillHub 侧完成，详见「帮助」。
            </p>
            <form
              className="grid gap-[calc(10px*var(--density-scale))]"
              onSubmit={(event) => {
                event.preventDefault();
                void installSkill();
              }}
            >
              <label htmlFor="skill-hub-identifier">Skill 名称</label>
              <input
                id="skill-hub-identifier"
                type="text"
                value={installIdentifier}
                onChange={(event) => setInstallIdentifier(event.target.value)}
                placeholder="例如 vulnerability_info"
                autoFocus
                required
              />
              {installError ? <p className="error-text">{installError}</p> : null}
              <div className="button-row cron-action-zone">
                <button type="submit" disabled={installPending || !installIdentifier.trim()}>
                  {installPending ? "安装中…" : "安装"}
                </button>
                <button type="button" className="ghost-button" disabled={installPending} onClick={() => setInstallOpen(false)}>
                  取消
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>

      <div
        className={helpOpen ? "cron-create-modal-overlay active" : "cron-create-modal-overlay"}
        onClick={() => setHelpOpen(false)}
        aria-hidden={!helpOpen}
      >
        <div
          className="cron-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-label="SkillHub 集成帮助"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="cron-detail-modal-header">
            <h4>如何从 SkillHub 集成 Skill</h4>
            <button type="button" className="ghost-button cron-raw-modal-close" onClick={() => setHelpOpen(false)} aria-label="关闭">
              ×
            </button>
          </div>
          <div className="cron-detail-modal-body">
            {/* TODO(skeleton): 手册正文待补充完整截图与步骤说明 */}
            <ol className="grid gap-[calc(8px*var(--density-scale))] pl-[calc(20px*var(--density-scale))]">
              <li>
                前往租户的 SkillHub 平台
                {hubStatus?.url ? (
                  <>
                    （
                    <a href={hubStatus.url} target="_blank" rel="noreferrer">
                      {hubStatus.url}
                    </a>
                    ）
                  </>
                ) : (
                  "（地址由管理员在配置文件 skill_hub.url 中设置）"
                )}
                ，注册资源并托管 token，完成授权后生成 Skill。
              </li>
              <li>回到本页面，点击右上角「+ 安装」，输入该 Skill 在 SkillHub 中的名称。</li>
              <li>点击「安装」，系统会从 SkillHub 下载该 Skill 并安装到本地技能目录。</li>
              <li>Skill 的内容与版本维护均在 SkillHub 侧完成；有更新后点「同步」拉取最新版本。</li>
              <li>不再需要时点「卸载」，将删除本地目录并移除安装记录。</li>
            </ol>
            {hubStatus && !hubStatus.api_key_configured ? (
              <p className="error-text">提示：尚未在配置文件中托管 SkillHub 凭据（skill_hub.api_key），安装可能失败。</p>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
