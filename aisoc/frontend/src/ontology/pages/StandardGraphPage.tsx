import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Database, Boxes, Share2, Scale, Tag, MessageSquare } from 'lucide-react';
import { useOntologyArtifact } from '../hooks/useOntology';
import { GraphCanvas } from '../components/GraphCanvas';
import { NodeDetail } from '../components/NodeDetail';
import { StatCard } from '../components/ui/Stat';
import { DOMAIN_META } from '../design/tokens';

const RELATION_HINTS = [
  { type: 'in', label: 'Intra-domain relation (soft solid line · always shown)', color: '#6E93C8', dash: false },
  { type: 'cross', label: 'Cross-domain relation (dashed · hover to highlight)', color: '#6FD3FF', dash: true },
];
const OBJECT_HINTS = [
  { label: 'L1 Top-level Domain', color: '#7E6BFF', shape: 'ellipse' },
  { label: 'L2 Sub-capability', color: '#39D1FF', shape: 'rect' },
  { label: 'L3 System', color: '#41D6A4', shape: 'diamond' },
  { label: 'L3 Data Source', color: '#41D6A4', shape: 'tag' },
  { label: 'L3 Tool', color: '#41D6A4', shape: 'hex' },
  { label: 'L3 Action', color: '#41D6A4', shape: 'triangle' },
];

export function StandardGraphPage() {
  const routerNavigate = useNavigate();
  const { data: standardGraph } = useOntologyArtifact('standard');
  const [selectedDomain, setSelectedDomain] = useState('ALL');
  const [selectedNode, setSelectedNode] = useState<any | null>(null);

  const allNodes = standardGraph?.nodes || [];
  const allEdges = standardGraph?.edges || [];
  const edgeStats = standardGraph?.edge_stats || {};
  const layerCounts = standardGraph?.layer_counts || {};
  const domainDefs = useMemo(() => standardGraph?.domains || [], [standardGraph]);

  const nodesById = useMemo(() => {
    const m: Record<string, any> = {};
    allNodes.forEach((n: any) => { m[n.id] = n; });
    return m;
  }, [allNodes]);

  const subcapCount = layerCounts.subcapabilities ?? allNodes.filter((n: any) => n.type === 'SubCapability').length;
  const objectCount = layerCounts.objects ?? allNodes.filter((n: any) => n.type === 'Object').length;
  const semanticCount = edgeStats.semantic ?? allEdges.filter((e: any) => e.kind === 'semantic').length;

  // ALL view = full 3 layers grouped by domain (clusters). Single-domain = that domain's 3 layers.
  const nodes = useMemo(() => {
    if (selectedDomain === 'ALL') return allNodes;
    const domNodes = allNodes.filter((n: any) => n.type === 'Domain' && n.id === selectedDomain);
    const subNodes = allNodes.filter((n: any) => n.type === 'SubCapability' && n.domain === selectedDomain);
    const objIds = new Set<string>();
    subNodes.forEach((s: any) => (s.objects || []).forEach((o: string) => objIds.add(o)));
    const objNodes = allNodes.filter((n: any) => n.type === 'Object' && objIds.has(n.id));
    return [...domNodes, ...subNodes, ...objNodes];
  }, [allNodes, selectedDomain]);

  const visibleIds = useMemo(() => new Set(nodes.map((n: any) => n.id)), [nodes]);
  const edges = useMemo(
    () => (selectedDomain === 'ALL' ? allEdges : allEdges.filter((e: any) => visibleIds.has(e.source) && visibleIds.has(e.target))),
    [allEdges, visibleIds, selectedDomain]
  );

  const navigate = (id: string) => { if (nodesById[id]) setSelectedNode(nodesById[id]); };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 anim-fade-up flex-wrap">
        <div>
          <div className="eyebrow mb-1">Onboarding · Standard Ontology</div>
          <h1 className="text-3xl font-bold font-display text-gradient">AISOC Standard Graph</h1>
          <p className="text-sm text-[color:var(--ink-mid)] mt-1.5">
            Three-layer capability ontology (Core Domain → Sub-capability → L3 Object) · Source:
            <span className="font-mono text-[color:var(--ink-lo)]"> standard-graph_v3.json</span>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="chip" style={{ background: 'var(--accent-cyan-soft)', color: 'var(--accent-cyan)', borderColor: 'color-mix(in srgb, var(--accent-cyan) 30%, transparent)' }}>
              Schema · {standardGraph?.schema_version || standardGraph?.schema || 'N/A'}
            </span>
            <span className="chip" style={{ background: 'color-mix(in srgb, var(--accent-emerald) 12%, transparent)', color: 'var(--accent-emerald)', borderColor: 'color-mix(in srgb, var(--accent-emerald) 26%, transparent)' }}>
              Source · standard-graph_v3.json
            </span>
            {standardGraph?.layer_counts && (
              <span className="text-[color:var(--ink-lo)] font-mono">
                {standardGraph.layer_counts.domains}D / {standardGraph.layer_counts.subcapabilities} L2 / {standardGraph.layer_counts.objects} L3
              </span>
            )}
          </div>
        </div>
        <button className="btn-ghost" onClick={() => routerNavigate('/chat?quick=instruct_ontology')}>
          <MessageSquare className="h-4 w-4" />
          Ask AI
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Weight Total" value={standardGraph?.weight_total ?? '—'} sub="Total sub-capability weight" accent="#38bdf8" icon={<Scale className="h-4 w-4" />} />
        <StatCard label="Domains" value={domainDefs.length} sub="① Core Domains" accent="#8B7CF6" icon={<Database className="h-4 w-4" />} />
        <StatCard label="Sub-capabilities" value={subcapCount} sub="② Sub-capabilities" accent="#6ee7b7" icon={<Boxes className="h-4 w-4" />} />
        <StatCard label="Objects" value={objectCount} sub="③ L3 Objects (shared)" accent="#D4A64A" icon={<Tag className="h-4 w-4" />} />
        <StatCard label="Semantic Links" value={semanticCount} sub={`${edgeStats.cross_domain ?? 0} cross-domain`} accent="#fb923c" icon={<Share2 className="h-4 w-4" />} />
      </div>

      {/* (a) domain filter uses NAMES not ids */}
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost" data-active={selectedDomain === 'ALL'} onClick={() => setSelectedDomain('ALL')}>All</button>
        {domainDefs.map((d: any) => (
          <button key={d.id} className="btn-ghost" data-active={selectedDomain === d.id} onClick={() => setSelectedDomain(d.id)}>
            <span className="h-2 w-2 rounded-full" style={{ background: DOMAIN_META[d.id]?.color || '#94A3B8' }} />
            {d.name_zh || DOMAIN_META[d.id]?.label_zh || d.id}
          </button>
        ))}
      </div>

      {/* (3)(4) graph with floating overlay detail panel (~1/5 width) on top layer */}
      <div className="relative">
        <div className="glass p-3">
          <GraphCanvas
            nodes={nodes} edges={edges} onSelect={setSelectedNode} selectedId={selectedNode?.id}
            colorMode="domain" height={640} showEdgeLabels={false} cluster={selectedDomain === 'ALL'}
          />
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-2 pt-3 text-xs">
            <span className="eyebrow">Edge Legend</span>
            {RELATION_HINTS.map((r) => (
              <span key={r.type} className="flex items-center gap-1.5 text-[color:var(--ink-mid)]">
                <span className="inline-block h-0 w-6" style={{ borderTop: `2px ${r.dash ? 'dashed' : 'solid'} ${r.color}` }} />{r.label}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-2 pt-2 text-xs">
            <span className="eyebrow">Node Legend</span>
            {OBJECT_HINTS.map((o) => (
              <span key={o.label} className="flex items-center gap-1.5 text-[color:var(--ink-mid)]">
                {o.shape === 'ellipse' && <span className="inline-block h-3 w-3 rounded-full border-2" style={{ borderColor: o.color, background: `${o.color}33` }} />}
                {o.shape === 'rect' && <span className="inline-block h-3 w-3 rounded-sm border-2" style={{ borderColor: o.color, background: `${o.color}29` }} />}
                {o.shape === 'diamond' && <span className="inline-block h-2.5 w-2.5 rotate-45" style={{ background: o.color, boxShadow: `0 0 8px ${o.color}66` }} />}
                {o.shape === 'tag' && <span className="inline-block h-2.5 w-3" style={{ background: o.color, boxShadow: `0 0 8px ${o.color}66`, clipPath: 'polygon(0 0, 78% 0, 100% 50%, 78% 100%, 0 100%)' }} />}
                {o.shape === 'hex' && <span className="inline-flex items-center justify-center text-[11px] leading-none" style={{ color: o.color, textShadow: `0 0 8px ${o.color}66` }}>⬡</span>}
                {o.shape === 'triangle' && <span className="inline-flex items-center justify-center text-[11px] leading-none" style={{ color: o.color, textShadow: `0 0 8px ${o.color}66` }}>△</span>}
                {o.label}
              </span>
            ))}
            <span className="text-[color:var(--ink-lo)]">· Unified 3-color scheme: L1 purple / L2 cyan / L3 green; layers distinguished by shape</span>
          </div>
        </div>
        {/* floating overlay detail — top layer, glass, ~1/5 width */}
        {selectedNode && (
          <div className="absolute top-6 left-6 w-1/4 min-w-[360px] max-w-[560px] z-30 anim-fade-in" style={{ height: 588 }}>
            <NodeDetail
              node={selectedNode} layout="overlay"
              ctx={{ nodesById, edges: allEdges, mode: 'standard' }}
              onNavigate={navigate} onClose={() => setSelectedNode(null)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
