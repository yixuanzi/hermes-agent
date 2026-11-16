// Shared design tokens for the WORKAGENT Ontology onboarding UI (v3 / 3-layer).

// ── Layer-1 domain palette (v3: D1–D9). Cohesive deep-tech arc, cool→warm. ──
export const DOMAIN_META: Record<string, { color: string; label_en: string; label_zh: string }> = {
  D1: { color: '#8B7CF6', label_en: 'WORKAGENT Foundation', label_zh: 'WORKAGENT底座' },
  D2: { color: '#4C9BFF', label_en: 'Data & Knowledge', label_zh: '数据与知识' },
  D3: { color: '#2FD6A6', label_en: 'Investigation & Triage', label_zh: '安全调查与研判' },
  D4: { color: '#31C8E0', label_en: 'Threat Hunting', label_zh: '主动威胁狩猎' },
  D5: { color: '#C08CF7', label_en: 'Threat Intelligence', label_zh: '威胁情报管理' },
  D6: { color: '#F4B740', label_en: 'Vulnerability & Exposure', label_zh: '漏洞与暴露面管理' },
  D7: { color: '#FF8A5B', label_en: 'Incident Response', label_zh: '事件响应与自动化处置' },
  D8: { color: '#FF6F91', label_en: 'Adversary Emulation', label_zh: '攻击模拟与检测验证' },
  D9: { color: '#9FB2CC', label_en: 'SecOps Metrics & Reporting', label_zh: '安全运营度量与报告' },
  // legacy ids (older snapshots) — kept for backward-compat
  D0: { color: '#8B7CF6', label_en: 'AI Orchestration', label_zh: 'AI 编排与知识底座' },
  ORG: { color: '#4C9BFF', label_en: 'Organization', label_zh: '组织与业务' },
  AST: { color: '#2FD6A6', label_en: 'Asset & Identity', label_zh: '资产与身份' },
  NET: { color: '#31C8E0', label_en: 'Network & Exposure', label_zh: '网络与暴露面' },
  ING: { color: '#4C9BFF', label_en: 'Data Ingestion', label_zh: '数据接入' },
  DET: { color: '#F4B740', label_en: 'Detection', label_zh: '检测分析' },
  IR: { color: '#FF8A5B', label_en: 'Incident Response', label_zh: '事件响应' },
  TI: { color: '#C08CF7', label_en: 'Threat Intelligence', label_zh: '威胁情报' },
  AI: { color: '#8B7CF6', label_en: 'AI Capability', label_zh: 'AI 能力层' },
  GOV: { color: '#9FB2CC', label_en: 'Data & Governance', label_zh: '数据底座与治理' },
};

export const domainColor = (d?: string) => DOMAIN_META[d || '']?.color || '#7C8DA6';

// ── Layer-3 object-type palette — a SEPARATE register (jewel tones) so the 3
// layers never collide in color. L1=domain arc, L2=cyan tint, L3=type jewels. ──
export const OBJECT_TYPE_META: Record<string, { color: string; label: string; shape: string }> = {
  system: { color: '#5B8DEF', label: '对接系统', shape: 'round-diamond' },
  data_source: { color: '#3FB9A0', label: '数据源', shape: 'round-tag' },
  tool: { color: '#D4A64A', label: '工具', shape: 'round-hexagon' },
  action: { color: '#B072E8', label: '动作', shape: 'round-triangle' },
};

export const objectTypeColor = (t?: string) => OBJECT_TYPE_META[t || '']?.color || '#7C8DA6';

// ── Per-layer visual register (shape + a distinct fill when not domain-colored) ──
export const LAYER_META: Record<number, { shape: string; label: string; accent: string }> = {
  1: { shape: 'round-hexagon', label: '① 核心域', accent: '#E7ECF6' },   // domains: hexagon, domain-hued
  2: { shape: 'round-rectangle', label: '② 二级功能', accent: '#7FE9FF' }, // subcaps: rounded-rect, cyan tint
  3: { shape: 'ellipse', label: '③ 三级对象', accent: '#B7C4DA' },        // objects: type shapes override
};

export const STATUS_META: Record<string, { color: string; label: string; chip: string }> = {
  satisfied: { color: '#2FD6A6', label: 'Satisfied', chip: 'chip-satisfied' },
  partial: { color: '#F4B740', label: 'Partial', chip: 'chip-partial' },
  missing: { color: '#FF6B8A', label: 'Missing', chip: 'chip-missing' },
  extra: { color: '#B69CF7', label: 'Extra', chip: 'chip-extra' },
};

export const statusColor = (s?: string) => STATUS_META[s || '']?.color || '#5D7292';
