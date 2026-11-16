// Shared design tokens for the AISOC Ontology onboarding UI (v3 / 3-layer).

// ── Layer-1 domain palette (v3: D1–D9). Cohesive deep-tech arc, cool→warm. ──
export const DOMAIN_META: Record<string, { color: string; label_en: string; label_zh: string }> = {
  D1: { color: '#8B7CF6', label_en: 'AISOC Foundation', label_zh: 'AISOC Foundation' },
  D2: { color: '#4C9BFF', label_en: 'Data & Knowledge', label_zh: 'Data & Knowledge' },
  D3: { color: '#6ee7b7', label_en: 'Investigation & Triage', label_zh: 'Investigation & Triage' },
  D4: { color: '#31C8E0', label_en: 'Threat Hunting', label_zh: 'Threat Hunting' },
  D5: { color: '#C08CF7', label_en: 'Threat Intelligence', label_zh: 'Threat Intelligence' },
  D6: { color: '#fcd34d', label_en: 'Vulnerability & Exposure', label_zh: 'Vulnerability & Exposure' },
  D7: { color: '#fb923c', label_en: 'Incident Response', label_zh: 'Incident Response' },
  D8: { color: '#fda4af', label_en: 'Adversary Emulation', label_zh: 'Adversary Emulation' },
  D9: { color: '#9FB2CC', label_en: 'SecOps Metrics & Reporting', label_zh: 'SecOps Metrics & Reporting' },
  // legacy ids (older snapshots) — kept for backward-compat
  D0: { color: '#8B7CF6', label_en: 'AI Orchestration', label_zh: 'AI Orchestration' },
  ORG: { color: '#4C9BFF', label_en: 'Organization', label_zh: 'Organization' },
  AST: { color: '#6ee7b7', label_en: 'Asset & Identity', label_zh: 'Asset & Identity' },
  NET: { color: '#31C8E0', label_en: 'Network & Exposure', label_zh: 'Network & Exposure' },
  ING: { color: '#4C9BFF', label_en: 'Data Ingestion', label_zh: 'Data Ingestion' },
  DET: { color: '#fcd34d', label_en: 'Detection', label_zh: 'Detection' },
  IR: { color: '#fb923c', label_en: 'Incident Response', label_zh: 'Incident Response' },
  TI: { color: '#C08CF7', label_en: 'Threat Intelligence', label_zh: 'Threat Intelligence' },
  AI: { color: '#8B7CF6', label_en: 'AI Capability', label_zh: 'AI Capability' },
  GOV: { color: '#9FB2CC', label_en: 'Data & Governance', label_zh: 'Data & Governance' },
};

export const domainColor = (d?: string) => DOMAIN_META[d || '']?.color || '#7C8DA6';

// ── Layer-3 object-type palette — a SEPARATE register (jewel tones) so the 3
// layers never collide in color. L1=domain arc, L2=cyan tint, L3=type jewels. ──
export const OBJECT_TYPE_META: Record<string, { color: string; label: string; shape: string }> = {
  system: { color: '#5B8DEF', label: 'System', shape: 'round-diamond' },
  data_source: { color: '#3FB9A0', label: 'Data Source', shape: 'round-tag' },
  tool: { color: '#D4A64A', label: 'Tool', shape: 'round-hexagon' },
  action: { color: '#B072E8', label: 'Action', shape: 'round-triangle' },
};

export const objectTypeColor = (t?: string) => OBJECT_TYPE_META[t || '']?.color || '#7C8DA6';

// ── Per-layer visual register (shape + a distinct fill when not domain-colored) ──
export const LAYER_META: Record<number, { shape: string; label: string; accent: string }> = {
  1: { shape: 'round-hexagon', label: '① Core Domain', accent: '#E7ECF6' },   // domains: hexagon, domain-hued
  2: { shape: 'round-rectangle', label: '② Sub-capability', accent: '#7dd3fc' }, // subcaps: rounded-rect, cyan tint
  3: { shape: 'ellipse', label: '③ L3 Object', accent: '#B7C4DA' },        // objects: type shapes override
};

export const STATUS_META: Record<string, { color: string; label: string; chip: string }> = {
  satisfied: { color: '#6ee7b7', label: 'Satisfied', chip: 'chip-satisfied' },
  partial: { color: '#fcd34d', label: 'Partial', chip: 'chip-partial' },
  missing: { color: '#fda4af', label: 'Missing', chip: 'chip-missing' },
  extra: { color: '#a78bfa', label: 'Extra', chip: 'chip-extra' },
};

export const statusColor = (s?: string) => STATUS_META[s || '']?.color || '#5D7292';
