import { useEffect, useState } from "react";

import { PageMissionHeader } from "../components/PageMissionHeader";
import { StateBlock } from "../components/StateBlock";
import { fetchJSON } from "../lib/api";

type McpServerTransport = "http" | "stdio" | "unknown";

type McpServerSummary = {
  name: string;
  transport: McpServerTransport;
  url: string | null;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  auth: string | null;
  enabled: boolean;
  tools: string[] | null;
};

type McpToolInfo = { name: string; description?: string };

type McpTestResult = {
  ok: boolean;
  error?: string;
  tools: McpToolInfo[];
  prompts?: number;
  resources?: number;
};

type EnvRow = { key: string; value: string };

type FormMode = "create" | "edit";

type FormState = {
  name: string;
  transport: "http" | "stdio";
  url: string;
  command: string;
  argsText: string;
  envRows: EnvRow[];
  auth: "none" | "header";
  bearerToken: string;
  enabled: boolean;
};

const EMPTY_FORM: FormState = {
  name: "",
  transport: "http",
  url: "",
  command: "",
  argsText: "",
  envRows: [],
  auth: "none",
  bearerToken: "",
  enabled: true,
};

function summaryToForm(server: McpServerSummary): FormState {
  return {
    name: server.name,
    transport: server.transport === "stdio" ? "stdio" : "http",
    url: server.url || "",
    command: server.command || "",
    argsText: server.args.join("\n"),
    envRows: Object.keys(server.env).map((key) => ({ key, value: "" })),
    auth: server.auth === "header" ? "header" : "none",
    bearerToken: "",
    enabled: server.enabled,
  };
}

export function McpServersPage() {
  const [servers, setServers] = useState<McpServerSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailName, setDetailName] = useState("");

  const [actionPending, setActionPending] = useState("");
  const [actionError, setActionError] = useState("");

  const [formOpen, setFormOpen] = useState(false);
  const [formMode, setFormMode] = useState<FormMode>("create");
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formPending, setFormPending] = useState(false);
  const [formError, setFormError] = useState("");

  const [testResults, setTestResults] = useState<Record<string, McpTestResult>>({});
  const [testPendingName, setTestPendingName] = useState("");
  const [toolSelections, setToolSelections] = useState<Record<string, Record<string, boolean>>>({});
  const [toolsSavePending, setToolsSavePending] = useState(false);
  const [toolsSaveMessage, setToolsSaveMessage] = useState("");

  const [reloadPending, setReloadPending] = useState(false);
  const [reloadMessage, setReloadMessage] = useState("");

  const detailServer = servers.find((server) => server.name === detailName) || null;

  async function loadServers() {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchJSON<McpServerSummary[]>("/api/mcp-servers");
      setServers(payload || []);
    } catch {
      setError("加载 MCP 服务器列表失败。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadServers();
  }, []);

  useEffect(() => {
    if (!servers.some((server) => server.name === detailName)) {
      setDetailName("");
    }
  }, [servers, detailName]);

  useEffect(() => {
    if (!formOpen && !detailName) return;
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (formOpen) setFormOpen(false);
      else setDetailName("");
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [formOpen, detailName]);

  // A server's tool list should already be visible as soon as its details
  // modal is opened, not only after the user clicks "测试连接" manually.
  useEffect(() => {
    if (!detailName) return;
    if (testResults[detailName] || testPendingName === detailName) return;
    void testServer(detailName);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailName]);

  function openDetail(name: string) {
    setDetailName(name);
  }

  function closeDetail() {
    setDetailName("");
  }

  function openCreateForm() {
    setFormMode("create");
    setForm(EMPTY_FORM);
    setFormError("");
    setFormOpen(true);
  }

  function openEditForm(server: McpServerSummary) {
    setFormMode("edit");
    setForm(summaryToForm(server));
    setFormError("");
    setFormOpen(true);
  }

  function updateEnvRow(index: number, field: "key" | "value", value: string) {
    setForm((current) => ({
      ...current,
      envRows: current.envRows.map((row, rowIndex) => (rowIndex === index ? { ...row, [field]: value } : row)),
    }));
  }

  function addEnvRow() {
    setForm((current) => ({ ...current, envRows: [...current.envRows, { key: "", value: "" }] }));
  }

  function removeEnvRow(index: number) {
    setForm((current) => ({ ...current, envRows: current.envRows.filter((_, rowIndex) => rowIndex !== index) }));
  }

  async function submitForm() {
    const name = form.name.trim();
    if (!name) {
      setFormError("请填写服务器名称。");
      return;
    }
    setFormPending(true);
    setFormError("");
    try {
      const env: Record<string, string> = {};
      for (const row of form.envRows) {
        const key = row.key.trim();
        if (key) env[key] = row.value;
      }
      const args = form.argsText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      const body = {
        url: form.transport === "http" ? form.url.trim() : "",
        command: form.transport === "stdio" ? form.command.trim() : "",
        args,
        env,
        auth: form.auth,
        bearer_token: form.bearerToken.trim() || null,
        enabled: form.enabled,
      };
      if (formMode === "create") {
        await fetchJSON("/api/mcp-servers", { method: "POST", body: JSON.stringify({ name, ...body }) });
      } else {
        await fetchJSON(`/api/mcp-servers/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(body) });
      }
      setFormOpen(false);
      await loadServers();
    } catch (err) {
      setFormError(err instanceof Error && err.message ? err.message : "保存 MCP 服务器失败。");
    } finally {
      setFormPending(false);
    }
  }

  async function removeServer(name: string) {
    if (!window.confirm(`删除 MCP 服务器 "${name}"？此操作不可撤销。`)) return;
    setActionPending(`${name}:delete`);
    setActionError("");
    try {
      await fetchJSON(`/api/mcp-servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      await loadServers();
    } catch {
      setActionError(`删除服务器 "${name}" 失败。`);
    } finally {
      setActionPending("");
    }
  }

  async function toggleEnabled(server: McpServerSummary) {
    setActionPending(`${server.name}:enabled`);
    setActionError("");
    try {
      await fetchJSON(`/api/mcp-servers/${encodeURIComponent(server.name)}/enabled`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !server.enabled }),
      });
      await loadServers();
    } catch {
      setActionError(`更新服务器 "${server.name}" 的启用状态失败。`);
    } finally {
      setActionPending("");
    }
  }

  async function testServer(name: string) {
    setTestPendingName(name);
    setActionError("");
    try {
      const result = await fetchJSON<McpTestResult>(`/api/mcp-servers/${encodeURIComponent(name)}/test`, {
        method: "POST",
      });
      setTestResults((current) => ({ ...current, [name]: result }));
      if (result.ok) {
        const server = servers.find((item) => item.name === name);
        const allowList = server?.tools;
        const selection: Record<string, boolean> = {};
        for (const tool of result.tools) {
          selection[tool.name] = allowList ? allowList.includes(tool.name) : true;
        }
        setToolSelections((current) => ({ ...current, [name]: selection }));
      }
      setToolsSaveMessage("");
    } catch {
      setActionError(`测试服务器 "${name}" 失败。`);
    } finally {
      setTestPendingName("");
    }
  }

  function toggleToolSelected(name: string, toolName: string) {
    setToolSelections((current) => ({
      ...current,
      [name]: { ...current[name], [toolName]: !current[name]?.[toolName] },
    }));
  }

  async function saveToolSelection(name: string) {
    const testResult = testResults[name];
    const selection = toolSelections[name];
    if (!testResult || !selection) return;
    setToolsSavePending(true);
    setToolsSaveMessage("");
    setActionError("");
    try {
      const checked = testResult.tools.filter((tool) => selection[tool.name]).map((tool) => tool.name);
      const tools = checked.length === testResult.tools.length ? null : checked;
      await fetchJSON(`/api/mcp-servers/${encodeURIComponent(name)}/tools`, {
        method: "PUT",
        body: JSON.stringify({ tools }),
      });
      await loadServers();
      setToolsSaveMessage("工具选择已保存。");
    } catch {
      setActionError(`保存服务器 "${name}" 的工具选择失败。`);
    } finally {
      setToolsSavePending(false);
    }
  }

  async function reload() {
    setReloadPending(true);
    setReloadMessage("");
    setActionError("");
    try {
      await fetchJSON("/api/mcp-servers/reload", { method: "POST" });
      setReloadMessage("已触发 MCP 服务器重新发现。");
    } catch {
      setActionError("触发重载失败。");
    } finally {
      setReloadPending(false);
    }
  }

  const detailTestResult = detailServer ? testResults[detailServer.name] : undefined;
  const detailToolSelection = detailServer ? toolSelections[detailServer.name] : undefined;

  return (
    <section className="skills-workbench-page">
      <PageMissionHeader
        title="MCP 服务器管理"
        subtitle="管理 AISOC agent 运行时使用的 MCP 服务器：新增、编辑、测试连接、工具选择与重载。"
        status={<span className="status-badge">{servers.length} 个服务器</span>}
        actions={
          <>
            <button type="button" className="ghost-button" disabled={reloadPending} onClick={() => void reload()}>
              {reloadPending ? "重载中…" : "重载"}
            </button>
            <button type="button" onClick={openCreateForm}>
              新增服务器
            </button>
          </>
        }
      />

      {error ? <StateBlock kind="error" title="加载失败" message={error} /> : null}
      {reloadMessage ? <p>{reloadMessage}</p> : null}
      {actionError ? <p className="error-text">{actionError}</p> : null}

      <article className="detail-panel">
        <div className="skills-list-head">
          <h3>服务器列表</h3>
        </div>
        {loading ? <StateBlock kind="loading" title="加载中" message="正在获取 /api/mcp-servers。" /> : null}
        {!loading && servers.length === 0 ? (
          <StateBlock kind="empty" title="暂无 MCP 服务器" message="点击右上角「新增服务器」创建第一个。" />
        ) : null}
        <ul className="list-grid cron-jobs-grid">
          {servers.map((server) => (
            <li key={server.name} className="cron-job-card">
              <div className="cron-job-card-head">
                <strong>{server.name}</strong>
                <span className={server.enabled ? "status-badge status-live" : "status-badge"}>
                  {server.enabled ? "已启用" : "已停用"}
                </span>
              </div>
              <p className="subtle-copy min-w-0 break-all">{server.transport === "stdio" ? server.command : server.url || "--"}</p>
              <p className="subtle-copy min-w-0 break-all">工具：{server.tools ? `${server.tools.length} 个已选` : "全部"}</p>
              <div className="cron-card-actions flex-wrap">
                <button
                  type="button"
                  className="ghost-button cron-icon-button cron-icon-button-detail"
                  disabled={actionPending === `${server.name}:enabled`}
                  onClick={() => void toggleEnabled(server)}
                >
                  {actionPending === `${server.name}:enabled` ? "…" : server.enabled ? "停用" : "启用"}
                </button>
                <button
                  type="button"
                  className="ghost-button cron-icon-button cron-icon-button-detail"
                  onClick={() => openEditForm(server)}
                >
                  编辑
                </button>
                <button
                  type="button"
                  className="ghost-button cron-icon-button cron-icon-button-detail"
                  disabled={actionPending === `${server.name}:delete`}
                  onClick={() => void removeServer(server.name)}
                >
                  {actionPending === `${server.name}:delete` ? "…" : "删除"}
                </button>
                <button type="button" className="cron-icon-button-detail" onClick={() => openDetail(server.name)}>
                  查看
                </button>
              </div>
            </li>
          ))}
        </ul>
      </article>

      <div
        className={detailServer ? "cron-detail-modal-overlay active" : "cron-detail-modal-overlay"}
        onClick={closeDetail}
        aria-hidden={!detailServer}
      >
        {detailServer ? (
          <div className="cron-detail-modal" role="dialog" aria-modal="true" aria-label={detailServer.name} onClick={(event) => event.stopPropagation()}>
            <div className="cron-detail-modal-header">
              <h4>{detailServer.name}</h4>
              <div className="button-row">
                <button
                  type="button"
                  className="ghost-button"
                  disabled={testPendingName === detailServer.name}
                  onClick={() => void testServer(detailServer.name)}
                >
                  {testPendingName === detailServer.name ? "测试中…" : "测试连接"}
                </button>
                <button type="button" className="ghost-button cron-raw-modal-close" onClick={closeDetail} aria-label="关闭">
                  ×
                </button>
              </div>
            </div>
            <div className="cron-detail-modal-body">
              <div className="min-w-0">
                <p className="subtle-copy min-w-0 break-all">Transport: {detailServer.transport}</p>
                {detailServer.transport === "stdio" ? (
                  <p className="subtle-copy min-w-0 break-all">
                    Command: {detailServer.command} {detailServer.args.join(" ")}
                  </p>
                ) : (
                  <p className="subtle-copy min-w-0 break-all">URL: {detailServer.url}</p>
                )}
                {Object.keys(detailServer.env).length > 0 ? (
                  <div className="subtle-copy min-w-0">
                    <p>Env:</p>
                    <ul className="flex flex-col gap-1">
                      {Object.entries(detailServer.env).map(([key, value]) => (
                        <li key={key} className="min-w-0 break-all">
                          {key} = {value || "(空)"}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>

              <div className="skills-detail-main">
                {testPendingName === detailServer.name ? (
                  <StateBlock kind="loading" title="正在测试连接" message="正在连接并获取工具列表…" />
                ) : detailTestResult ? (
                  detailTestResult.ok ? (
                    <div className="flex flex-col gap-[calc(8px*var(--density-scale))]">
                      <h4>发现的工具（{detailTestResult.tools.length}）</h4>
                      {detailTestResult.tools.length === 0 ? (
                        <p className="subtle-copy">该服务器未暴露任何工具。</p>
                      ) : (
                        <ul className="flex flex-col gap-[calc(6px*var(--density-scale))]">
                          {detailTestResult.tools.map((tool) => (
                            <li key={tool.name} className="detail-panel min-w-0 p-[calc(8px*var(--density-scale))]">
                              <label className="flex items-center gap-2">
                                <input
                                  type="checkbox"
                                  checked={Boolean(detailToolSelection?.[tool.name])}
                                  onChange={() => toggleToolSelected(detailServer.name, tool.name)}
                                />
                                <strong className="min-w-0 break-all">{tool.name}</strong>
                              </label>
                              {tool.description ? <p className="subtle-copy min-w-0 break-all !m-0">{tool.description}</p> : null}
                            </li>
                          ))}
                        </ul>
                      )}
                      {detailTestResult.tools.length > 0 ? (
                        <div className="button-row">
                          <button type="button" disabled={toolsSavePending} onClick={() => void saveToolSelection(detailServer.name)}>
                            {toolsSavePending ? "保存中…" : "保存工具选择"}
                          </button>
                        </div>
                      ) : null}
                      {toolsSaveMessage ? <p>{toolsSaveMessage}</p> : null}
                    </div>
                  ) : (
                    <StateBlock kind="error" title="连接测试失败" message={detailTestResult.error || "未知错误"} />
                  )
                ) : (
                  <StateBlock kind="empty" title="尚未测试" message="点击「测试连接」获取该服务器的工具列表。" />
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div
        className={formOpen ? "cron-create-modal-overlay active" : "cron-create-modal-overlay"}
        onClick={() => setFormOpen(false)}
        aria-hidden={!formOpen}
      >
        <div
          className="cron-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-label={formMode === "create" ? "新增 MCP 服务器" : "编辑 MCP 服务器"}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="cron-detail-modal-header">
            <h4>{formMode === "create" ? "新增 MCP 服务器" : `编辑 ${form.name}`}</h4>
            <button type="button" className="ghost-button cron-raw-modal-close" onClick={() => setFormOpen(false)} aria-label="关闭">
              ×
            </button>
          </div>
          <div className="cron-detail-modal-body">
            <form
              className="grid gap-[calc(10px*var(--density-scale))]"
              onSubmit={(event) => {
                event.preventDefault();
                void submitForm();
              }}
            >
              <label htmlFor="mcp-name">名称</label>
              <input
                id="mcp-name"
                type="text"
                value={form.name}
                disabled={formMode === "edit"}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                required
              />

              <label htmlFor="mcp-transport">类型</label>
              <select
                id="mcp-transport"
                value={form.transport}
                onChange={(event) => setForm((current) => ({ ...current, transport: event.target.value as "http" | "stdio" }))}
              >
                <option value="http">HTTP / SSE</option>
                <option value="stdio">stdio（本地命令）</option>
              </select>

              {form.transport === "http" ? (
                <>
                  <label htmlFor="mcp-url">URL</label>
                  <input
                    id="mcp-url"
                    type="text"
                    value={form.url}
                    onChange={(event) => setForm((current) => ({ ...current, url: event.target.value }))}
                    placeholder="https://example.com/mcp"
                    required
                  />
                  <label htmlFor="mcp-auth">鉴权方式</label>
                  <select
                    id="mcp-auth"
                    value={form.auth}
                    onChange={(event) => setForm((current) => ({ ...current, auth: event.target.value as "none" | "header" }))}
                  >
                    <option value="none">无</option>
                    <option value="header">Bearer Token</option>
                  </select>
                  {form.auth === "header" ? (
                    <>
                      <label htmlFor="mcp-bearer-token">Bearer Token</label>
                      <input
                        id="mcp-bearer-token"
                        type="password"
                        value={form.bearerToken}
                        onChange={(event) => setForm((current) => ({ ...current, bearerToken: event.target.value }))}
                        placeholder={formMode === "edit" ? "留空则不修改" : ""}
                      />
                    </>
                  ) : null}
                </>
              ) : (
                <>
                  <label htmlFor="mcp-command">命令</label>
                  <input
                    id="mcp-command"
                    type="text"
                    value={form.command}
                    onChange={(event) => setForm((current) => ({ ...current, command: event.target.value }))}
                    placeholder="npx"
                    required
                  />
                  <label htmlFor="mcp-args">参数（每行一个）</label>
                  <textarea
                    id="mcp-args"
                    value={form.argsText}
                    onChange={(event) => setForm((current) => ({ ...current, argsText: event.target.value }))}
                    rows={3}
                  />
                  <label>环境变量</label>
                  <div className="flex flex-col gap-[calc(6px*var(--density-scale))]">
                    {form.envRows.map((row, index) => (
                      <div key={index} className="flex items-center gap-[calc(6px*var(--density-scale))]">
                        <input type="text" value={row.key} placeholder="KEY" onChange={(event) => updateEnvRow(index, "key", event.target.value)} />
                        <input
                          type="password"
                          value={row.value}
                          placeholder={formMode === "edit" ? "留空则不修改" : "VALUE"}
                          onChange={(event) => updateEnvRow(index, "value", event.target.value)}
                        />
                        <button type="button" className="ghost-button" onClick={() => removeEnvRow(index)}>
                          移除
                        </button>
                      </div>
                    ))}
                    <button type="button" className="ghost-button" onClick={addEnvRow}>
                      + 添加环境变量
                    </button>
                  </div>
                </>
              )}

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))}
                />
                启用
              </label>

              <div className="button-row cron-action-zone">
                <button type="submit" disabled={formPending}>
                  {formPending ? "保存中…" : "保存"}
                </button>
                <button type="button" className="ghost-button" disabled={formPending} onClick={() => setFormOpen(false)}>
                  取消
                </button>
              </div>
              {formError ? <p className="error-text">{formError}</p> : null}
            </form>
          </div>
        </div>
      </div>
    </section>
  );
}
