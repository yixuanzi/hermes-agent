"""HTML for the ``/man`` A2A management console (see ``a2a_management``).

Kept as a module rather than a static file so it ships with the package.  The
page is self-contained under a nonce CSP: no inline handlers or style
attributes, and user-supplied values only ever reach the DOM via
``textContent``.
"""

from __future__ import annotations


def render_management_page(*, enabled: bool, nonce: str) -> str:
    return (
        _PAGE.replace("__NONCE__", nonce)
        .replace("__ENABLED__", "true" if enabled else "false")
        .replace("__DISABLED_ATTR__", "" if enabled else " disabled")
        .replace(
            "__AVAILABILITY__",
            "Sign in with the dedicated admin token."
            if enabled
            else "Management is not enabled: set WORKAGENT_A2A_ADMIN_TOKEN and restart.",
        )
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WORKAGENT A2A Console</title>
<style nonce="__NONCE__">
:root {
  color-scheme: dark;
  --bg: #0f1316; --panel: #161c20; --panel-2: #1b2227; --line: #2f393f; --line-2: #45525a;
  --text: #dce4e8; --muted: #93a1a8; --accent: #9fd0e6; --accent-ink: #0d1417;
  --ok: #7fc8a0; --warn: #e6c27a; --danger: #e07070; --danger-bg: #8f2b2b;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 var(--sans); }
.wrap { width: min(1080px, calc(100% - 32px)); margin: 32px auto 64px; }
header.top { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.tag { font: 11px/1 var(--mono); letter-spacing: .12em; color: var(--accent); text-transform: uppercase; }
h1 { margin: 8px 0 0; font-size: 1.35rem; font-weight: 650; letter-spacing: -.01em; }
h2 { margin: 0; font-size: .95rem; font-weight: 650; }
p { margin: 0; color: var(--muted); }
code, .mono { font-family: var(--mono); font-size: .86em; }
.session { display: flex; align-items: center; gap: 10px; color: var(--muted); font: 12px var(--mono); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
.panel { margin-top: 20px; border: 1px solid var(--line); background: var(--panel); }
.panel-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 18px; border-bottom: 1px solid var(--line); }
.panel-head .lede { flex: 1 1 320px; display: grid; gap: 4px; }
.panel-body { padding: 18px; }
.danger-zone { border-color: #5a2f2f; }
.danger-zone .panel-head { border-bottom-color: #5a2f2f; }
label { display: block; margin-bottom: 6px; font-weight: 600; font-size: .86rem; }
.hint { font-size: .8rem; color: var(--muted); margin-top: 6px; }
input[type=text], input[type=password], textarea {
  width: 100%; padding: 9px 10px; border: 1px solid var(--line-2); border-radius: 0; background: #0b0f12; color: var(--text); font: 13px var(--mono);
}
textarea { min-height: 150px; resize: vertical; line-height: 1.45; }
input:focus-visible, textarea:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.invalid { border-color: var(--danger) !important; }
button { min-height: 36px; padding: 7px 14px; border: 1px solid var(--line-2); border-radius: 0; background: var(--panel-2); color: var(--text); font: 600 13px var(--sans); cursor: pointer; }
button:hover:not(:disabled) { border-color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
button.danger { background: var(--danger-bg); border-color: var(--danger); color: #fff; }
button.ghost { background: transparent; }
button.small { min-height: 30px; padding: 4px 10px; font-size: 12px; }
button.link-danger { color: var(--danger); }
button:disabled { opacity: .45; cursor: not-allowed; }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.login { max-width: 460px; display: grid; gap: 12px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 11px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { font: 600 11px var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); background: var(--panel-2); }
tr:last-child td { border-bottom: 0; }
td.name { font-weight: 600; min-width: 120px; }
/* Identity takes whatever width is left and truncates; the rest stay on one line. */
td.identity { width: 100%; max-width: 0; }
td.nowrap { white-space: nowrap; }
.identity-text { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 12px var(--mono); color: var(--text); }
.sub { display: block; margin-top: 3px; color: var(--muted); font: 11px var(--mono); }
td.actions { white-space: nowrap; text-align: right; }
td.actions button + button { margin-left: 6px; }
.badge { display: inline-block; padding: 2px 7px; border: 1px solid; font: 600 11px var(--mono); letter-spacing: .04em; }
.badge.on { color: var(--ok); border-color: #3f6f55; }
.badge.off { color: var(--muted); border-color: var(--line-2); }
.switch { display: inline-flex; align-items: center; gap: 8px; margin: 0; font-weight: 500; cursor: pointer; }
.switch input { width: 16px; height: 16px; accent-color: var(--accent); }
.empty { padding: 36px 18px; text-align: center; color: var(--muted); }
.empty strong { display: block; color: var(--text); margin-bottom: 6px; }
#status { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); max-width: calc(100% - 32px); padding: 9px 14px; border: 1px solid var(--line-2); background: #0b0f12; font-size: 13px; opacity: 0; transition: opacity .15s; pointer-events: none; }
#status.show { opacity: 1; }
#status.error { border-color: var(--danger); color: #ffd6d6; }
dialog { width: min(620px, calc(100% - 32px)); padding: 0; border: 1px solid var(--line-2); background: var(--panel); color: var(--text); }
dialog::backdrop { background: rgba(4, 7, 9, .72); }
dialog form, dialog .dialog-body { display: grid; gap: 16px; padding: 20px; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 10px; }
.secret-box { display: flex; gap: 8px; }
.secret-box input { flex: 1; }
pre.snippet { margin: 0; padding: 10px; overflow-x: auto; border: 1px solid var(--line); background: #0b0f12; font: 12px/1.5 var(--mono); white-space: pre-wrap; word-break: break-all; }
.callout { padding: 10px 12px; border-left: 3px solid var(--warn); background: #241f14; color: #efdcae; font-size: 13px; }
.field-error { color: var(--danger); font-size: .8rem; margin-top: 6px; min-height: 1em; }
[hidden] { display: none !important; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (max-width: 860px) {
  thead { display: none; }
  table, tbody, tr, td { display: block; width: 100%; }
  tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
  td { border: 0; padding: 4px 14px; }
  td.identity { max-width: none; width: auto; }
  td.actions { text-align: left; padding-top: 8px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <span class="tag">Service / workagent-a2a</span>
      <h1>A2A Management Console</h1>
    </div>
    <div class="session" id="session" hidden>
      <span class="dot" aria-hidden="true"></span>
      <span id="session-text">Signed in</span>
      <button class="small ghost" id="signout-button" type="button">Sign out</button>
    </div>
  </header>

  <section class="panel" id="login-panel" aria-labelledby="login-title">
    <div class="panel-head"><div class="lede"><h2 id="login-title">Administrator sign-in</h2><p>__AVAILABILITY__</p></div></div>
    <div class="panel-body">
      <form class="login" id="login-form">
        <div>
          <label for="admin-token">Admin token</label>
          <input id="admin-token" type="password" autocomplete="off" spellcheck="false"__DISABLED_ATTR__>
        </div>
        <div><button class="primary" id="login-button" type="submit"__DISABLED_ATTR__>Sign in</button></div>
      </form>
    </div>
  </section>

  <div id="console" hidden>
    <section class="panel" aria-labelledby="keys-title">
      <div class="panel-head">
        <div class="lede">
          <h2 id="keys-title">Personal MCP keys</h2>
          <p>Each key signs in to <code>/mcp</code> as one person. Its identity is put in front of every task that person starts, as <code>&lt;source&gt;…&lt;/source&gt;</code>, and the key sees only its own tasks and sessions.</p>
        </div>
        <button class="primary" id="new-key-button" type="button">New key</button>
      </div>
      <div id="keys-empty" class="empty" hidden><strong>No personal keys yet</strong>Create one to give someone their own MCP access.</div>
      <table id="keys-table" hidden>
        <thead><tr><th>Name</th><th>Identity</th><th>Key</th><th>Status</th><th>Last used</th><th><span class="sr">Actions</span></th></tr></thead>
        <tbody id="keys-body"></tbody>
      </table>
    </section>

    <section class="panel danger-zone" aria-labelledby="restart-title">
      <div class="panel-head">
        <div class="lede"><h2 id="restart-title">Process control</h2><p>Restart starts one replacement process and stops this one. Running tasks are interrupted.</p></div>
        <button class="danger" id="restart-button" type="button">Restart A2A</button>
      </div>
    </section>
  </div>
</div>

<dialog id="key-dialog" aria-labelledby="key-dialog-title">
  <form id="key-form" method="dialog">
    <h2 id="key-dialog-title">New personal key</h2>
    <div>
      <label for="key-name">Name</label>
      <input id="key-name" type="text" maxlength="100" autocomplete="off" placeholder="Guisheng Guo — Feishu">
      <div class="field-error" id="key-name-error"></div>
    </div>
    <div>
      <label for="key-source">Identity (JSON object)</label>
      <textarea id="key-source" spellcheck="false" placeholder='{"platform":"feishu","channel":"oc_…","uid":"…","uname":"…"}'></textarea>
      <div class="field-error" id="key-source-error"></div>
      <div class="row hint"><span>Put in front of each task as <code>&lt;source&gt;{…}&lt;/source&gt;</code>. Fields are up to you; the agent reads <code>platform</code>, <code>uid</code>, <code>uname</code> and <code>channel</code>.</span><button class="small ghost" id="format-button" type="button">Format</button></div>
    </div>
    <label class="switch" id="key-enabled-row" hidden><input id="key-enabled" type="checkbox"> Enabled</label>
    <div class="dialog-actions">
      <button class="ghost" id="key-cancel" type="button">Cancel</button>
      <button class="primary" id="key-save" type="submit">Create key</button>
    </div>
  </form>
</dialog>

<dialog id="secret-dialog" aria-labelledby="secret-title">
  <div class="dialog-body">
    <h2 id="secret-title">Key created</h2>
    <div class="callout">Copy this key now. It is stored only as a hash and will not be shown again.</div>
    <div>
      <label for="secret-value">Personal MCP key for <span id="secret-owner"></span></label>
      <div class="secret-box"><input id="secret-value" type="text" readonly spellcheck="false"><button id="copy-secret" type="button">Copy</button></div>
    </div>
    <div>
      <label>Add to Claude Code</label>
      <pre class="snippet" id="secret-command"></pre>
      <div class="row hint"><button class="small ghost" id="copy-command" type="button">Copy command</button></div>
    </div>
    <div class="dialog-actions"><button class="primary" id="secret-done" type="button">Done</button></div>
  </div>
</dialog>

<div id="status" role="status" aria-live="polite"></div>

<script nonce="__NONCE__">
(() => {
  "use strict";
  const MANAGEMENT_ENABLED = __ENABLED__;
  const RESTART_PHRASE = "RESTART A2A";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  let accessToken = "";
  let expiresAt = 0;
  let clock = 0;
  let keys = [];
  let editingId = null;
  let statusTimer = 0;

  function notify(message, isError) {
    const box = $("status");
    box.textContent = message;
    box.classList.toggle("error", !!isError);
    box.classList.add("show");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => box.classList.remove("show"), isError ? 6000 : 3500);
  }

  class ApiError extends Error {}

  async function api(method, path, body) {
    const headers = { "Authorization": `Bearer ${accessToken}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
    if (response.status === 401) { signOut("Your session expired. Sign in again."); throw new ApiError("Session expired."); }
    if (response.status === 204) return null;
    let payload = null;
    try { payload = await response.json(); } catch (_e) { payload = null; }
    if (!response.ok) throw new ApiError((payload && payload.detail) || `Request failed (${response.status}).`);
    return payload;
  }

  // ── session ────────────────────────────────────────────────
  function tick() {
    const left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
    if (left === 0) { signOut("Your session expired. Sign in again."); return; }
    $("session-text").textContent = `Admin session · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`;
  }

  function signIn(payload) {
    accessToken = payload.access_token;
    expiresAt = Date.now() + payload.expires_in * 1000;
    $("login-panel").hidden = true;
    $("console").hidden = false;
    $("session").hidden = false;
    clearInterval(clock);
    clock = setInterval(tick, 1000);
    tick();
    loadKeys();
  }

  function signOut(message) {
    accessToken = "";
    clearInterval(clock);
    for (const d of document.querySelectorAll("dialog[open]")) d.close();
    $("console").hidden = true;
    $("session").hidden = true;
    $("login-panel").hidden = false;
    $("keys-body").replaceChildren();
    if (message) notify(message, true);
    $("admin-token").focus();
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!MANAGEMENT_ENABLED) return;
    const button = $("login-button");
    button.disabled = true;
    try {
      const response = await fetch("/man/api/auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: $("admin-token").value }) });
      if (!response.ok) throw new Error(response.status === 401 ? "Invalid admin token." : "Management is unavailable.");
      $("admin-token").value = "";
      signIn(await response.json());
    } catch (error) {
      notify(error.message, true);
    } finally {
      button.disabled = !MANAGEMENT_ENABLED;
    }
  });
  $("signout-button").addEventListener("click", () => signOut(""));

  // ── keys ───────────────────────────────────────────────────
  const fmtTime = (epoch) => epoch ? new Date(epoch * 1000).toLocaleString() : "Never";
  const compact = (source) => JSON.stringify(source);

  async function loadKeys() {
    try {
      keys = (await api("GET", "/man/api/keys")).keys;
      renderKeys();
    } catch (error) { if (accessToken) notify(error.message, true); }
  }

  function renderKeys() {
    const body = $("keys-body");
    body.replaceChildren();
    $("keys-empty").hidden = keys.length > 0;
    $("keys-table").hidden = keys.length === 0;
    for (const key of keys) {
      const row = el("tr");
      const name = el("td", "name", key.name);
      name.append(el("span", "sub", `id ${key.id}`));
      const identity = el("td", "identity");
      const text = el("span", "identity-text", compact(key.source));
      text.title = JSON.stringify(key.source, null, 2);
      identity.append(text);
      const hint = el("td", "mono nowrap", key.key_hint);
      const state = el("td", "nowrap");
      const toggle = el("label", "switch");
      const box = el("input");
      box.type = "checkbox";
      box.checked = key.enabled;
      box.setAttribute("aria-label", `${key.enabled ? "Disable" : "Enable"} ${key.name}`);
      box.addEventListener("change", () => setEnabled(key, box));
      toggle.append(box, el("span", `badge ${key.enabled ? "on" : "off"}`, key.enabled ? "ACTIVE" : "DISABLED"));
      state.append(toggle);
      const used = el("td", "mono nowrap", fmtTime(key.last_used_at));
      used.append(el("span", "sub", `created ${fmtTime(key.created_at)}`));
      const actions = el("td", "actions");
      const edit = el("button", "small", "Edit");
      edit.type = "button";
      edit.addEventListener("click", () => openEditor(key));
      const regen = el("button", "small", "Regenerate");
      regen.type = "button";
      regen.addEventListener("click", () => regenerate(key));
      const del = el("button", "small ghost link-danger", "Delete");
      del.type = "button";
      del.addEventListener("click", () => removeKey(key));
      actions.append(edit, regen, del);
      row.append(name, identity, hint, state, used, actions);
      body.append(row);
    }
  }

  async function setEnabled(key, box) {
    box.disabled = true;
    try {
      await api("PATCH", `/man/api/keys/${encodeURIComponent(key.id)}`, { enabled: box.checked });
      notify(`${key.name} ${box.checked ? "enabled" : "disabled"}.`);
      await loadKeys();
    } catch (error) {
      box.checked = !box.checked;
      box.disabled = false;
      notify(error.message, true);
    }
  }

  async function regenerate(key) {
    if (!window.confirm(`Regenerate the key for "${key.name}"? The current key stops working immediately.`)) return;
    try {
      const result = await api("POST", `/man/api/keys/${encodeURIComponent(key.id)}/regenerate`);
      await loadKeys();
      showSecret(result, "Key regenerated");
    } catch (error) { notify(error.message, true); }
  }

  async function removeKey(key) {
    if (!window.confirm(`Delete the key for "${key.name}"? Anyone using it loses access immediately. This cannot be undone.`)) return;
    try {
      await api("DELETE", `/man/api/keys/${encodeURIComponent(key.id)}`);
      notify(`Deleted ${key.name}.`);
      await loadKeys();
    } catch (error) { notify(error.message, true); }
  }

  // ── editor dialog ──────────────────────────────────────────
  function parseSource(text) {
    let value = text.trim();
    const match = value.match(/^<source>([\\s\\S]*)<\\/source>$/);
    if (match) value = match[1].trim();
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || Object.keys(parsed).length === 0) throw new Error("Must be a non-empty JSON object.");
    return parsed;
  }

  function validateSource() {
    const area = $("key-source");
    try { parseSource(area.value); area.classList.remove("invalid"); $("key-source-error").textContent = ""; return true; }
    catch (error) { area.classList.add("invalid"); $("key-source-error").textContent = error.message; return false; }
  }

  function openEditor(key) {
    editingId = key ? key.id : null;
    $("key-dialog-title").textContent = key ? `Edit ${key.name}` : "New personal key";
    $("key-save").textContent = key ? "Save changes" : "Create key";
    $("key-name").value = key ? key.name : "";
    $("key-source").value = key ? JSON.stringify(key.source, null, 2) : "";
    $("key-enabled-row").hidden = !key;
    $("key-enabled").checked = key ? key.enabled : true;
    for (const id of ["key-name-error", "key-source-error"]) $(id).textContent = "";
    for (const id of ["key-name", "key-source"]) $(id).classList.remove("invalid");
    $("key-dialog").showModal();
    $("key-name").focus();
  }

  $("new-key-button").addEventListener("click", () => openEditor(null));
  $("key-cancel").addEventListener("click", () => $("key-dialog").close());
  $("key-source").addEventListener("input", () => { if ($("key-source").classList.contains("invalid")) validateSource(); });
  $("format-button").addEventListener("click", () => {
    if (validateSource()) $("key-source").value = JSON.stringify(parseSource($("key-source").value), null, 2);
  });

  $("key-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("key-name").value.trim();
    $("key-name").classList.toggle("invalid", !name);
    $("key-name-error").textContent = name ? "" : "Name is required.";
    const sourceOk = validateSource();
    if (!name || !sourceOk) return;
    const body = { name, source: parseSource($("key-source").value) };
    const save = $("key-save");
    save.disabled = true;
    try {
      if (editingId) {
        body.enabled = $("key-enabled").checked;
        await api("PATCH", `/man/api/keys/${encodeURIComponent(editingId)}`, body);
        $("key-dialog").close();
        notify(`Saved ${name}.`);
        await loadKeys();
      } else {
        const result = await api("POST", "/man/api/keys", body);
        $("key-dialog").close();
        await loadKeys();
        showSecret(result, "Key created");
      }
    } catch (error) {
      notify(error.message, true);
    } finally {
      save.disabled = false;
    }
  });

  // ── one-time secret reveal ─────────────────────────────────
  function showSecret(result, title) {
    $("secret-title").textContent = title;
    $("secret-owner").textContent = result.key.name;
    $("secret-value").value = result.secret;
    const server = `${location.origin}/mcp`;
    $("secret-command").textContent = `claude mcp add --transport http hermes-workagent ${server} --header "Authorization: Bearer ${result.secret}"`;
    $("secret-dialog").showModal();
    $("secret-value").select();
  }

  async function copyText(text, label) {
    try { await navigator.clipboard.writeText(text); notify(`${label} copied.`); }
    catch (_e) { notify("Copy is blocked here; select the text and copy it manually.", true); }
  }

  $("copy-secret").addEventListener("click", () => copyText($("secret-value").value, "Key"));
  $("copy-command").addEventListener("click", () => copyText($("secret-command").textContent, "Command"));
  $("secret-done").addEventListener("click", () => { $("secret-value").value = ""; $("secret-dialog").close(); });
  $("secret-dialog").addEventListener("close", () => { $("secret-value").value = ""; $("secret-command").textContent = ""; });

  // ── restart ────────────────────────────────────────────────
  let reloadRequested = false;
  async function pollForReplacement(oldPid) {
    let sawDowntime = false;
    while (!reloadRequested) {
      try {
        const response = await fetch("/health", { cache: "no-store" });
        if (!response.ok) throw new Error("unhealthy");
        const health = await response.json();
        if (health.pid !== oldPid || (sawDowntime && health.pid == null)) { reloadRequested = true; window.location.reload(); return; }
      } catch (_e) { sawDowntime = true; }
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
  }

  $("restart-button").addEventListener("click", async () => {
    if (!window.confirm("Restart the WORKAGENT A2A process? Running tasks will be interrupted.")) return;
    const phrase = window.prompt(`Type ${RESTART_PHRASE} to confirm.`) || "";
    if (phrase !== RESTART_PHRASE) { notify("Restart cancelled: the confirmation phrase did not match.", true); return; }
    const button = $("restart-button");
    button.disabled = true;
    try {
      const payload = await api("POST", "/man/api/restart");
      notify("Restart requested. Waiting for the replacement process…");
      void pollForReplacement(payload.pid);
    } catch (error) {
      button.disabled = false;
      notify(error.message, true);
    }
  });
})();
</script>
</body>
</html>
"""
