import { Agent, AgentDraft, AgentStatus, RoutingRule, RoutingRuleDraft, RoutingRuleStatus } from '../types';

type BackendAgentStatus = 'active' | 'idle' | 'offline';
type BackendRoutingStatus = 'active' | 'inactive';

export type BackendAgent = {
  agent_id: string;
  url: string;
  description: string;
  headers: Record<string, string>;
  status: BackendAgentStatus;
  extcapabilities: string[];
};

export type BackendAgentList = {
  agents: BackendAgent[];
};

export type BackendOverviewAgent = {
  agent_id: string;
  url: string;
  description: string;
  status: BackendAgentStatus;
  extcapabilities: string[];
  headers?: Record<string, string>;
};

export type BackendOverviewAgentList = {
  agents: BackendOverviewAgent[];
};

export type BackendRoutingRule = {
  id: string;
  name: string;
  policy: string;
  status: BackendRoutingStatus;
};

export type BackendRoutingRuleList = {
  rules: BackendRoutingRule[];
};

const AGENT_STATUS_TO_UI: Record<BackendAgentStatus, AgentStatus> = {
  active: 'Active',
  idle: 'Idle',
  offline: 'Offline',
};

const AGENT_STATUS_TO_API: Record<AgentStatus, BackendAgentStatus> = {
  Active: 'active',
  Idle: 'idle',
  Offline: 'offline',
};

const ROUTING_STATUS_TO_UI: Record<BackendRoutingStatus, RoutingRuleStatus> = {
  active: 'Enabled',
  inactive: 'Disabled',
};

const ROUTING_STATUS_TO_API: Record<RoutingRuleStatus, BackendRoutingStatus> = {
  Enabled: 'active',
  Disabled: 'inactive',
};

export function backendAgentToUi(agent: BackendAgent | BackendOverviewAgent): Agent {
  const headers = Object.entries(agent.headers || {}).map(([key, value]) => ({ key, value }));
  return {
    id: agent.agent_id,
    name: agent.agent_id,
    type: 'agent',
    description: agent.description,
    status: AGENT_STATUS_TO_UI[agent.status],
    tasksCount: 0,
    lastUpdated: 'Synced',
    skillDescription: agent.extcapabilities.join('\n'),
    a2aAddr: agent.url,
    headers,
    extCapabilities: agent.extcapabilities,
  };
}

export function uiAgentDraftToApi(agent: AgentDraft): Omit<BackendAgent, 'agent_id'> {
  const headers: Record<string, string> = {};
  for (const entry of agent.headers) {
    const key = entry.key.trim();
    const value = entry.value.trim();
    if (key && value) {
      headers[key] = value;
    }
  }

  return {
    url: agent.url.trim(),
    description: agent.description.trim(),
    headers,
    status: AGENT_STATUS_TO_API[agent.status],
    extcapabilities: agent.extCapabilities.map((capability) => capability.trim()).filter(Boolean),
  };
}

export function backendRuleToUi(rule: BackendRoutingRule, index: number): RoutingRule {
  return {
    id: rule.id,
    priority: index + 1,
    ruleName: rule.name,
    agentId: 'all',
    conditions: rule.policy,
    actions: 'Global Fallback',
    status: ROUTING_STATUS_TO_UI[rule.status],
    updateTime: 'Synced',
  };
}

export function uiRoutingDraftToApi(rule: RoutingRuleDraft): Omit<BackendRoutingRule, 'id'> {
  return {
    name: rule.name.trim(),
    policy: rule.policy.trim(),
    status: ROUTING_STATUS_TO_API[rule.status],
  };
}
