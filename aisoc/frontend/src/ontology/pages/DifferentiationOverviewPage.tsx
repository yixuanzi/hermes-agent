import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Radar, AlertTriangle, CheckCircle2, MessageSquare, MinusCircle, PlusCircle, ArrowUpRight, Search } from 'lucide-react';
import { ontologyApi, type OntologyScanResponse } from '../api/ontology';
import { useOntologyArtifact, useOntologyOverview, useOntologyRoadmap } from '../hooks/useOntology';
import { GraphCanvas } from '../components/GraphCanvas';
import { NodeDetail } from '../components/NodeDetail';
import { StatCard, ProgressBar } from '../components/ui/Stat';
import { STATUS_META, DOMAIN_META } from '../design/tokens';
import { EmptyScan, isNoScan404 } from '../components/EmptyScan';

const STATE_FILTERS = ['ALL', 'satisfied', 'partial', 'missing', 'extra'] as const;
const SCAN_STAGES = ['Discover Resources', 'Parse Config', 'Extract Evidence', 'Map Ontology', 'Score', 'Render Diff'];
const JOB_STAGE_LABELS: Record<string, string> = {
  queued: 'Queued',
  collecting_evidence: 'Collecting Evidence',
  judging_with_ai: 'AI Judgment',
  scoring_and_gap_analysis: 'Diff Scoring',
  completed: 'Completed',
  failed: 'Failed',
};

export function DifferentiationOverviewPage() {
  const routerNavigate = useNavigate();
  const qc = useQueryClient();
  const overviewQuery = useOntologyOverview();
  const overview = overviewQuery.data;
  const { data: mappedGraph } = useOntologyArtifact('mapped');
  const { data: standardGraph } = useOntologyArtifact('standard');
  const { data: scorecard } = useOntologyArtifact('scorecard');
  const { data: roadmap } = useOntologyRoadmap();
  const [scanState, setScanState] = useState<OntologyScanResponse | null>(null);
  const [scanBusy, setScanBusy] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [stage, setStage] = useState(-1);
  const [selectedNode, setSelectedNode] = useState<any | null>(null);
  const [stateFilter, setStateFilter] = useState<(typeof STATE_FILTERS)[number]>('ALL');
  const [remDomain, setRemDomain] = useState('ALL');
  const [remSubcap, setRemSubcap] = useState('ALL');
  const [remQuery, setRemQuery] = useState('');
  const [expandedRem, setExpandedRem] = useState<Record<string, boolean>>({});

  // real backend job progress
  useEffect(() => {
    if (!scanBusy) {
      if (stage >= 0) setTimeout(() => setStage(-1), 600);
      return;
    }
    const stageMap: Record<string, number> = {
      queued: 0,
      collecting_evidence: 1,
      judging_with_ai: 3,
      scoring_and_gap_analysis: 5,
      completed: 5,
      failed: 5,
    };
    const next = scanState?.stage ? (stageMap[scanState.stage] ?? 0) : 0;
    setStage(next);
  }, [scanBusy, scanState?.stage]);

  // Stops the polling loop below from hitting the backend once this component
  // instance is gone — e.g. the user switched to Standard Graph or Ontology
  // Chat. Without this, navigating away and back N times leaves N orphaned
  // polling loops running concurrently (their setState calls are silently
  // dropped by React, but the network requests keep firing).
  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  // Poll an in-flight job to completion. Shared by runScan() (after creating
  // a fresh job) and the mount-time reattach effect below (after discovering
  // one is already running) so both paths get identical progress/error
  // handling.
  const watchJob = async (initial: OntologyScanResponse) => {
    try {
      setScanBusy(true);
      setScanError(null);
      setScanState(initial);
      let latest = initial;
      let stableMisses = 0;
      for (let i = 0; i < 600; i += 1) {
        if (cancelledRef.current) return;
        if (latest.status === 'completed' || latest.status === 'failed') break;
        await new Promise((r) => setTimeout(r, 1000));
        if (cancelledRef.current) return;
        try {
          latest = await ontologyApi.scanStatus(latest.job_id);
          setScanState(latest);
          stableMisses = 0;
        } catch (err) {
          stableMisses += 1;
          // backend may be restarting or job response may momentarily fail; do not surface failure immediately
          if (stableMisses >= 5) throw err;
        }
      }
      if (latest.status === 'failed') throw new Error(latest.error || 'scan failed');
      if (latest.status !== 'completed') throw new Error('scan is still running; AI judgment has not finished yet');
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['ontologyOverview'] }),
        qc.invalidateQueries({ queryKey: ['ontologyArtifact'] }),
        qc.invalidateQueries({ queryKey: ['ontologyRoadmap'] }),
        qc.invalidateQueries({ queryKey: ['ontologyHeatmap'] }),
      ]);
      if (!cancelledRef.current) setScanState(latest);
    } catch (e: any) {
      if (!cancelledRef.current) setScanError(String(e?.message || e || 'unknown error'));
    } finally {
      if (!cancelledRef.current) setScanBusy(false);
    }
  };

  const runScan = async () => {
    const job = await ontologyApi.scan();
    await watchJob(job);
  };

  // On mount (including after navigating away from this page and back),
  // check the backend for a scan that's already running — the job survives
  // in a daemon thread + on-disk state file regardless of whether this React
  // component instance was ever around to see it. This is what makes the
  // progress box reappear instead of silently vanishing on remount.
  useEffect(() => {
    let ignore = false;
    ontologyApi
      .scanActive()
      .then((job) => {
        if (ignore || cancelledRef.current) return;
        if (job.status === 'queued' || job.status === 'running') {
          watchJob(job);
        }
      })
      .catch(() => {
        // 404 (nothing running) is the common case — nothing to do.
      });
    return () => {
      ignore = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rawMapped = useMemo(() => mappedGraph?.mapped_nodes || [], [mappedGraph]);
  const extraNodes = useMemo(() => mappedGraph?.extra_nodes || [], [mappedGraph]);
  const stdById = useMemo(() => {
    const m: Record<string, any> = {};
    (standardGraph?.nodes || []).forEach((n: any) => { m[n.id] = n; });
    return m;
  }, [standardGraph]);
  const mappedById = useMemo(() => {
    const m: Record<string, any> = {};
    rawMapped.forEach((n: any) => { m[n.id] = n; });
    return m;
  }, [rawMapped]);
  const nodesById = useMemo(() => {
    const m: Record<string, any> = {};
    (standardGraph?.nodes || []).forEach((n: any) => { m[n.id] = n; });
    return m;
  }, [standardGraph]);
  const allNodes = useMemo(
    () => rawMapped.map((n: any) => {
      const s = stdById[n.id] || {};
      return { ...s, ...n, definition: n.definition ?? s.definition, business_value: n.business_value ?? s.business_value, type: n.type ?? s.type, layer: n.layer ?? s.layer ?? 2 };
    }),
    [rawMapped, stdById]
  );
  const counts = mappedGraph?.status_counts || overview?.status_counts || {};
  const total = allNodes.length || 1;
  const score = overview?.score ?? scorecard?.completeness_score ?? 0;

  // (6) Extra nodes integrated into the MAIN graph (shown under ALL and the Extra filter)
  const extraAsNodes = useMemo(() => extraNodes.map((e: any) => ({
    id: e.id, label: e.name_zh, name_en: e.name_en, type: 'SubCapability', layer: 2,
    domain: 'EXTRA', status: 'extra', definition: e.definition, business_value: e.recommendation,
    importance_weight: 2.5, recommendation: e.recommendation, evidence: e.evidence,
  })), [extraNodes]);

  const nodes = useMemo(() => {
    let base: any[];
    if (stateFilter === 'ALL') base = [...allNodes, ...extraAsNodes];
    else if (stateFilter === 'extra') base = extraAsNodes;
    else base = allNodes.filter((n: any) => n.status === stateFilter);
    const activeDomains = new Set(base.map((n: any) => n.domain));
    const domainNodes = (standardGraph?.nodes || []).filter((n: any) => n.type === 'Domain' && activeDomains.has(n.id));
    return [...domainNodes, ...base];
  }, [allNodes, extraAsNodes, stateFilter, standardGraph]);
  const visibleIds = useMemo(() => new Set(nodes.map((n: any) => n.id)), [nodes]);
  const edges = useMemo(
    () => (standardGraph?.edges || []).filter((e: any) => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [standardGraph, visibleIds]
  );

  const navigate = (id: string) => { const n = nodesById[id] || allNodes.find((x: any) => x.id === id) || extraAsNodes.find((x: any) => x.id === id); if (n) setSelectedNode(n); };

  const gapItems = useMemo(() => {
    const items = roadmap?.items || [];
    const q = remQuery.trim().toLowerCase();
    return items.filter((rec: any) => {
      if (remDomain !== 'ALL' && rec.domain !== remDomain) return false;
      if (remSubcap !== 'ALL' && rec.node_id !== remSubcap) return false;
      if (!q) return true;
      const hay = [rec.node_id, rec.title, rec.domain, rec.gap, rec.action, rec.recommendation, ...(rec.ai_object_recommendations || [])].join(' ').toLowerCase();
      return hay.includes(q);
    });
  }, [roadmap, remDomain, remSubcap, remQuery]);
  const remDomains = useMemo(() => ['ALL', ...Array.from(new Set((roadmap?.items || []).map((x: any) => x.domain)))], [roadmap]);
  const remSubcaps = useMemo(() => {
    const base = (roadmap?.items || []).filter((x: any) => remDomain === 'ALL' || x.domain === remDomain);
    return [{ id: 'ALL', title: 'All sub-capabilities' }, ...base.map((x: any) => ({ id: x.node_id, title: x.title }))];
  }, [roadmap, remDomain]);

  // 无扫描快照：overview 404 → 展示空态并允许一键 scan（沿用 hermes-agent 的空态卡片）
  if (isNoScan404(overviewQuery.error)) {
    return (
      <div className="space-y-6">
        <div className="anim-fade-up">
          <div className="eyebrow mb-1">Onboarding · Environment Differentiation</div>
          <h1 className="text-3xl font-bold font-display text-gradient">Differentiation Overview</h1>
          <p className="text-sm text-[color:var(--ink-mid)] mt-1.5">
            This environment has not generated a scan snapshot yet. Run an Environment Scan to display the differentiation analysis, gaps, and recommendations.
          </p>
        </div>
        <EmptyScan
          onRunScan={runScan}
          scanPending={scanBusy}
          scanError={scanError}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 anim-fade-up flex-wrap">
        <div>
          <div className="eyebrow mb-1">Onboarding · Environment Differentiation</div>
          <h1 className="text-3xl font-bold font-display text-gradient">Differentiation Overview</h1>
          <p className="text-sm text-[color:var(--ink-mid)] mt-1.5">
            Real environment vs standard ontology differentiation analysis · Source:
            <span className="font-mono text-[color:var(--ink-lo)]"> mapped / gap / scorecard</span>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="chip" style={{ background: 'var(--accent-cyan-soft)', color: 'var(--accent-cyan)', borderColor: 'color-mix(in srgb, var(--accent-cyan) 30%, transparent)' }}>
              Schema · {overview?.standard_graph_schema || 'N/A'}
            </span>
            <span className="chip" style={{ background: 'color-mix(in srgb, var(--accent-emerald) 12%, transparent)', color: 'var(--accent-emerald)', borderColor: 'color-mix(in srgb, var(--accent-emerald) 26%, transparent)' }}>
              Source · {overview?.standard_graph_path?.split('/').pop() || 'N/A'}
            </span>
            {overview?.standard_graph_counts && (
              <span className="text-[color:var(--ink-lo)] font-mono">
                {overview.standard_graph_counts.domains ?? '?'}D / {overview.standard_graph_counts.subcapabilities ?? '?'} L2 / {overview.standard_graph_counts.objects ?? '?'} L3
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 min-w-[320px]">
          <div className="flex items-center gap-2">
            <button className="btn-ghost" onClick={() => routerNavigate('/chat?quick=instruct_ontology')}>
              <MessageSquare className="h-4 w-4" />
              Ask AI
            </button>
            <button className="btn-primary" disabled={scanBusy} onClick={runScan}>
              <Radar className={`h-4 w-4 ${scanBusy ? 'animate-spin' : ''}`} />
              {scanBusy ? 'Running differentiation analysis…' : 'Run Environment Scan'}
            </button>
          </div>
          {/* always show explicit scan feedback so the click is never silent */}
          {scanBusy ? (
            <div className="w-full glass px-3 py-2 text-left">
              <div className="flex items-center justify-between text-xs text-[color:var(--ink-lo)] mb-1.5">
                <span>Running environment scan and differentiation analysis…</span>
                <span>{scanState?.batch_label || (scanState?.stage ? (JOB_STAGE_LABELS[scanState.stage] || scanState.stage) : SCAN_STAGES[Math.max(stage, 0)])}</span>
              </div>
              <ProgressBar ratio={scanState?.progress ?? ((Math.max(stage, 0) + 1) / SCAN_STAGES.length)} color="#38bdf8" />
              <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-[color:var(--ink-lo)] font-mono">
                <span>{scanState?.job_id || 'job: pending'}</span>
                <span>{Math.round((scanState?.progress ?? ((Math.max(stage, 0) + 1) / SCAN_STAGES.length)) * 100)}%</span>
              </div>
              {(scanState?.current_batch || scanState?.total_batches) && (
                <div className="mt-1 text-[10px] text-[color:var(--ink-lo)]">batch · {scanState.current_batch ?? 0} / {scanState.total_batches ?? '?'}</div>
              )}
              {scanState?.updated_at && (
                <div className="mt-1 text-[10px] text-[color:var(--ink-lo)]">last update · {scanState.updated_at}</div>
              )}
            </div>
          ) : scanState?.status === 'completed' ? (
            <div className="w-full glass px-3 py-2 text-left">
              <div className="text-xs text-[color:var(--ink-lo)]">Latest scan completed</div>
              <div className="text-sm text-[color:var(--ink-hi)] font-mono mt-0.5">{scanState?.scan_id} · score {scanState?.score}</div>
            </div>
          ) : scanError ? (
            <div className="w-full glass px-3 py-2 text-left border border-[color:color-mix(in_srgb,var(--accent-rose)_38%,transparent)]">
              <div className="text-xs text-[color:var(--accent-rose)] font-semibold">Scan failed</div>
              <div className="text-xs text-[color:var(--ink-mid)] mt-0.5">{scanError}</div>
            </div>
          ) : null}
          {/* (h) staged progress */}
          {stage >= 0 && (
            <div className="flex items-center gap-1.5">
              {SCAN_STAGES.map((s, i) => (
                <div key={s} className="flex items-center gap-1.5">
                  <span className="text-[10px]" style={{ color: i <= stage ? 'var(--accent-cyan)' : 'var(--ink-lo)' }}>{s}</span>
                  {i < SCAN_STAGES.length - 1 && <span className="h-px w-3" style={{ background: i < stage ? 'var(--accent-cyan)' : 'var(--stroke-soft)' }} />}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="glass p-5">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <div className="eyebrow">Completeness Score</div>
            <div className="mt-1 flex items-end gap-2">
              <span className="data-figure">{Number(score).toFixed(1)}</span>
              <span className="text-lg text-[color:var(--ink-lo)] mb-1">/ 100</span>
            </div>
          </div>
          <div className="flex-1 min-w-[240px] max-w-xl">
            <div className="flex justify-between text-xs text-[color:var(--ink-lo)] mb-1.5">
              <span>Mapped {total} nodes</span>
              <span>Last scan: <span className="font-mono">{overview?.latest_scan_id || 'N/A'}</span></span>
            </div>
            <ProgressBar ratio={Number(score) / 100} />
          </div>
        </div>
        {overview?.recent_scans?.length ? (
          <div className="mt-4 rounded-xl border px-3 py-3" style={{ borderColor: 'var(--stroke-soft)', background: 'rgba(255, 255, 255, 0.02)' }}>
            <div className="eyebrow mb-2">Recent Scan History</div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-2">
              {overview.recent_scans.slice(0, 5).map((s) => (
                <div key={s.scan_id} className="rounded-lg border px-3 py-2" style={{ borderColor: 'var(--stroke-soft)', background: 'var(--bg-glass)' }}>
                  <div className="text-[11px] text-[color:var(--ink-lo)] font-mono truncate">{s.scan_id}</div>
                  <div className="mt-1 text-sm text-[color:var(--ink-hi)] font-semibold">{typeof s.score === 'number' ? s.score.toFixed(1) : '—'}</div>
                  <div className="mt-1 text-[10px] text-[color:var(--ink-lo)]">S {s.status_counts?.satisfied ?? 0} · P {s.status_counts?.partial ?? 0} · M {s.status_counts?.missing ?? 0} · X {s.status_counts?.extra ?? 0}</div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StateCard status="satisfied" count={counts.satisfied ?? 0} total={total} icon={<CheckCircle2 className="h-4 w-4" />} />
        <StateCard status="partial" count={counts.partial ?? 0} total={total} icon={<AlertTriangle className="h-4 w-4" />} />
        <StateCard status="missing" count={counts.missing ?? 0} total={total} icon={<MinusCircle className="h-4 w-4" />} />
        <StateCard status="extra" count={counts.extra ?? extraNodes.length ?? 0} total={total} icon={<PlusCircle className="h-4 w-4" />} />
      </div>

      <div className="flex flex-wrap gap-2">
        {STATE_FILTERS.map((s) => (
          <button key={s} className="btn-ghost" data-active={stateFilter === s} onClick={() => setStateFilter(s)}>
            {s !== 'ALL' && <span className="h-2 w-2 rounded-full" style={{ background: STATUS_META[s]?.color }} />}
            {s === 'ALL' ? 'All' : STATUS_META[s].label}
          </button>
        ))}
      </div>

      {/* (6) main graph now includes Extra nodes; (3)(4) floating overlay detail (~1/5) */}
      <div className="relative">
        <div className="glass p-3">
          <GraphCanvas nodes={nodes} edges={edges} onSelect={setSelectedNode} selectedId={selectedNode?.id} colorMode="status" height={588} showEdgeLabels={false} cluster={stateFilter === 'ALL'} />
        </div>
        {selectedNode && (
          <div className="absolute top-6 left-6 w-1/4 min-w-[360px] max-w-[520px] z-30 anim-fade-in" style={{ height: 540 }}>
            <NodeDetail node={selectedNode} layout="overlay" ctx={{ nodesById, edges: standardGraph?.edges || [], mappedById, mode: 'diff' }} onNavigate={navigate} onClose={() => setSelectedNode(null)} />
          </div>
        )}
      </div>

      {/* Remediation */}
      <div className="glass p-5">
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <ArrowUpRight className="h-4 w-4" style={{ color: 'var(--accent-cyan)' }} />
          <h2 className="text-lg font-semibold text-[color:var(--ink-hi)]">Remediation</h2>
          <span className="text-xs text-[color:var(--ink-lo)]">Lists all sub-capabilities needing remediation and their L3 object AI gaps/recommendations</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[180px_220px_1fr] gap-3 mb-4">
          <select className="input" value={remDomain} onChange={(e) => { setRemDomain(e.target.value); setRemSubcap('ALL'); }}>
            {remDomains.map((d) => <option key={d} value={d}>{d === 'ALL' ? 'All top-level domains' : `${d} · ${DOMAIN_META[d]?.label_zh || d}`}</option>)}
          </select>
          <select className="input" value={remSubcap} onChange={(e) => setRemSubcap(e.target.value)}>
            {remSubcaps.map((s: any) => <option key={s.id} value={s.id}>{s.title}</option>)}
          </select>
          <label className="glass flex items-center gap-2 px-3 py-2 rounded-xl border" style={{ borderColor: 'var(--stroke-soft)' }}>
            <Search className="h-4 w-4 text-[color:var(--ink-lo)]" />
            <input className="bg-transparent outline-none w-full text-sm" placeholder="Filter by sub-capability / L3 object / AI gap / AI recommendation keyword" value={remQuery} onChange={(e) => setRemQuery(e.target.value)} />
          </label>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {gapItems.map((rec: any) => {
            const sm = STATUS_META[rec.status] || STATUS_META.partial;
            const dm = DOMAIN_META[rec.domain];
            const prio = rec.priority;
            const prioColor: Record<string, string> = { immediate: 'var(--accent-rose)', high: 'var(--accent-amber)', medium: 'var(--accent-blue)', low: 'var(--ink-lo)' };
            const mapped = mappedById[rec.node_id];
            const objectDetail = mapped?.object_detail || [];
            const objectGaps = objectDetail.filter((o: any) => (o.satisfaction ?? 0) < 0.95);
            const expanded = expandedRem[rec.node_id] ?? false;
            return (
              <div key={rec.node_id} className="rounded-xl border p-4 glass-hover flex flex-col gap-3" style={{ borderColor: 'var(--stroke-soft)' }}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium text-[color:var(--ink-hi)] truncate">{rec.title}</div>
                    <div className="text-xs font-mono text-[color:var(--ink-lo)] mt-0.5">{rec.node_id} · {dm?.label_zh || rec.domain}</div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {prio && <span className="chip" style={{ background: `color-mix(in srgb, ${prioColor[prio]} 12%, transparent)`, color: prioColor[prio], borderColor: `color-mix(in srgb, ${prioColor[prio]} 34%, transparent)`, textTransform: 'uppercase', fontSize: 10 }}>{prio}</span>}
                    <span className={`chip ${sm.chip}`}>{sm.label}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3 text-xs text-[color:var(--ink-lo)]">
                  <span>Weight <span className="text-[color:var(--ink-hi)]">{rec.importance_weight}</span></span>
                  <div className="flex-1"><ProgressBar ratio={rec.fulfillment_ratio ?? 0} color={sm.color} /></div>
                  <span>{Math.round((rec.fulfillment_ratio ?? 0) * 100)}%</span>
                </div>
                <div className="space-y-2 text-sm">
                  {rec.gap && <RecRow label="Gap" color="var(--accent-amber)">{rec.gap}</RecRow>}
                  {rec.action && <RecRow label="Recommendation" color="var(--accent-emerald)">{rec.action}</RecRow>}
                  {rec.ai_object_recommendations?.length > 0 && <RecRow label="AI Object-level Recommendations" color="var(--accent-cyan)">{rec.ai_object_recommendations.join('; ')}</RecRow>}
                </div>
                <div className="rounded-lg border p-3" style={{ borderColor: 'var(--stroke-soft)', background: 'rgba(255, 255, 255, 0.02)' }}>
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <div className="eyebrow">L3 Object Remediation List ({objectGaps.length})</div>
                    <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => setExpandedRem((prev) => ({ ...prev, [rec.node_id]: !expanded }))}>
                      {expanded ? 'Collapse' : 'Expand Details'}
                    </button>
                  </div>
                  {expanded ? (
                    <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1">
                      {objectGaps.length === 0 ? (
                        <div className="text-xs text-[color:var(--ink-lo)] italic">No L3 objects need remediation</div>
                      ) : objectGaps.map((o: any) => (
                        <div key={o.object_id} className="rounded-md border p-2.5" style={{ borderColor: 'var(--stroke-soft)' }}>
                          <div className="flex items-start justify-between gap-2">
                            <button className="text-left text-sm text-[color:var(--ink-hi)] hover:text-[color:var(--accent-cyan)]" onClick={() => navigate(o.object_id)}>{o.name_zh}</button>
                            {(() => { const sc = (o.satisfaction ?? 0) > 0 ? 'var(--accent-amber)' : 'var(--accent-rose)'; return (
                            <span className="chip" style={{ fontSize: 10, background: `color-mix(in srgb, ${sc} 12%, transparent)`, color: sc, borderColor: `color-mix(in srgb, ${sc} 34%, transparent)` }}>{(o.satisfaction ?? 0) > 0 ? 'Partial' : 'Missing'}</span>
                            ); })()}
                          </div>
                          {o.reason && <div className="mt-1.5 text-xs text-[color:var(--ink-mid)]"><span className="text-[color:var(--accent-amber)] font-semibold">AI Gap: </span>{o.reason}</div>}
                          {o.recommendation && <div className="mt-1 text-xs text-[color:var(--ink-mid)]"><span className="text-[color:var(--accent-emerald)] font-semibold">AI Recommendation: </span>{o.recommendation}</div>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {objectGaps.slice(0, 6).map((o: any) => (
                        <button key={o.object_id} className="chip" style={{ background: 'color-mix(in srgb, var(--ink-hi) 5%, transparent)', color: 'var(--ink-mid)', borderColor: 'var(--stroke-soft)' }} onClick={() => navigate(o.object_id)}>
                          {o.name_zh}
                        </button>
                      ))}
                      {objectGaps.length > 6 && <span className="text-xs text-[color:var(--ink-lo)]">+{objectGaps.length - 6} more</span>}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function RecRow({ label, color, children }: { label: string; color: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 leading-relaxed">
      <span className="shrink-0 text-[11px] font-semibold mt-0.5 px-1.5 rounded" style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}>{label}</span>
      <span className="text-[color:var(--ink-mid)]">{children}</span>
    </div>
  );
}

function StateCard({ status, count, total, icon }: { status: string; count: number; total: number; icon: React.ReactNode }) {
  const sm = STATUS_META[status];
  return (
    <div className="glass glass-hover p-4">
      <div className="flex items-center justify-between">
        <span className={`chip ${sm.chip}`}>{icon}{sm.label}</span>
        <span className="text-2xl font-bold font-display" style={{ color: sm.color }}>{count}</span>
      </div>
      <div className="mt-3"><ProgressBar ratio={count / total} color={sm.color} /></div>
      <div className="mt-1.5 text-xs text-[color:var(--ink-lo)]">{Math.round((count / total) * 100)}% of mapped nodes</div>
    </div>
  );
}
