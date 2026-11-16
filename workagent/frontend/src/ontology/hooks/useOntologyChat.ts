/**
 * Ontology Chat rides on the shared ``useAgentChat`` hook — same JSON-RPC
 * WebSocket client, same message/tool/approval/clarify plumbing that the
 * FloatingChat widget uses. This wrapper only adds:
 *
 *   1. seed ``role: "system"`` message —— constrain the agent to be an ontology
 *      consultant that must cite evidence via ``(node_id)``
 *   2. isolated storage prefix —— ``workagent.ontology-chat.*`` so tab caches don't
 *      collide with the FloatingChat widget
 *   3. stable seed title (via ``sessionCreateParams.title``) —— the shared
 *      hook's auto-title only rewrites the default ``"New Chat"`` string, so
 *      any non-default seed survives naturally without a "freeze" flag
 *
 * Profile is intentionally **not** hardcoded. By default the session inherits
 * the profile the workagent backend was launched with (e.g. ``-p search_agent``);
 * the workagent-ontology skill is discovered by NAME from whichever profile is
 * active, so migrating to a different profile name doesn't require code
 * changes. If a specific profile MUST be pinned (e.g. a locked-down deployment
 * where only ``workagent`` should host this session), set ``VITE_ONTOLOGY_PROFILE``
 * at build time — the session will then create with ``profile: <that value>``.
 *
 * Everything else (streaming, tools, interrupt, approval, clarify, reasoning,
 * markdown-ready message shapes) comes free from the shared hook.
 */
import { useEffect } from "react";

import { useAgentChat, type UseAgentChatOptions } from "../../lib/useAgentChat";

export const ONTOLOGY_SESSION_TITLE = "Ontology Consultant";

/** Refers to the skill by its stable ``name:`` in SKILL.md, not by the profile
 * directory it lives under. Renaming or migrating the profile does not break
 * this reference as long as the skill is discoverable in the active profile. */
export const ONTOLOGY_SKILL_NAME = "workagent-ontology";

export const ONTOLOGY_SYSTEM_INSTRUCTIONS = `你是 WORKAGENT 本体建设顾问。这个会话专门用来回答关于 WORKAGENT 能力本体（Ontology）、扫描完整度评分、能力缺口的问题。

规则：
1. 优先使用 ${ONTOLOGY_SKILL_NAME} skill 中的脚本（compile / scan / mapper / scoring / diff）来查证事实；不要凭记忆回答。
2. 每一条断言都要用 (node_id) 圆括号引用来自 mapped-graph 的证据；无证据就说"暂无直接证据"，然后建议下一步扫描/查询方向。
3. 保持回答简洁：4 段以内，中文回复。
4. 拒绝对未扫描过的域做主观评价；如果 mapper/scanner 说 partial，就说 partial。`;

/** Optional profile override at build time. Leave unset to inherit the
 * launch profile. Example: ``VITE_ONTOLOGY_PROFILE=workagent npm run build``. */
const PROFILE_OVERRIDE = (import.meta.env.VITE_ONTOLOGY_PROFILE || "").trim();

const ONTOLOGY_OPTIONS: UseAgentChatOptions = {
  storagePrefix: "workagent.ontology-chat",
  sessionCreateParams: {
    title: ONTOLOGY_SESSION_TITLE,
    source: "ontology-console",
    close_on_disconnect: false,
    ...(PROFILE_OVERRIDE ? { profile: PROFILE_OVERRIDE } : {}),
  },
  seedMessages: [{ role: "system", content: ONTOLOGY_SYSTEM_INSTRUCTIONS }],
};

export function useOntologyChat() {
  const chat = useAgentChat(ONTOLOGY_OPTIONS);

  // Auto-connect on mount if we have a cached session; otherwise the caller
  // (page) can decide to call connect() explicitly. This mirrors FloatingChat
  // but is unconditional here — Ontology Chat should always try to be online.
  useEffect(() => {
    if (chat.state.phase === "disconnected" || chat.state.phase === "connecting") {
      chat.connect();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return chat;
}
