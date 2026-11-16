import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import aegisConnection from '../assets/starmapping/aegis-connection.svg';
import argusEyes from '../assets/starmapping/argus-eyes.svg';
import themisScales from '../assets/starmapping/themis-scales.svg';
import lokiKnot from '../assets/starmapping/loki-knot.svg';
import vulcanAnvil from '../assets/starmapping/vulcan-anvil.svg';
import heimdallEye from '../assets/starmapping/heimdall-eye.svg';
import janusDuality from '../assets/starmapping/janus-duality.svg';
import wedjatEye from '../assets/starmapping/wedjat-eye.svg';
import StarmappingStarfieldCanvas from './StarmappingStarfieldCanvas';
import type { AppEntry, StarmappingTopology, TopologyAgentNode, TopologyStarKind } from '../types';

interface StarmappingTopologyProps {
  topology: StarmappingTopology | null;
  error: string;
  appEntries: AppEntry[];
  appEntriesLoading: boolean;
  appEntriesError: string;
  appEntriesLoaded: boolean;
}

type RenderNodeKind = 'center' | 'agent' | 'star';

interface RenderNode {
  id: string;
  parentId?: string;
  kind: RenderNodeKind;
  x: number;
  y: number;
  r: number;
  color: string;
  label: string;
  symbol: string;
  agent?: TopologyAgentNode;
  starKind?: TopologyStarKind;
  name: string;
  detail: string;
  integration?: string;
  required?: boolean;
}

const CANVAS = { width: 1000, height: 700, centerX: 500, centerY: 365, agentRadius: 184 };

const AGENT_COLORS: Record<string, string> = {
  'ai-soc': 'var(--aegis-starmap-accent)',
  'ai-grc': 'var(--aegis-starmap-success)',
  'ai-redteam': 'var(--aegis-starmap-danger)',
  'ai-sdlc': 'var(--aegis-starmap-warning)',
  'ai-ueba': 'var(--aegis-starmap-object)',
  'ai-itops': 'var(--aegis-starmap-focus)',
  'ai-web3': 'var(--aegis-starmap-actor)',
};

const STAR_COLORS: Record<TopologyStarKind, string> = {
  tool: 'var(--aegis-starmap-object)',
  api: 'var(--aegis-starmap-actor)',
  data: 'var(--aegis-starmap-muted)',
};

const TWINKLE_INTERVAL_MS = 2_000;
const TWINKLE_WAVE_STAGGER_MS = 800;
const TWINKLE_WAVE_LIFETIME_MS = 1_800;
const TWINKLE_BATCH_RATIO = 0.3;

const SYMBOL_ASSETS: Record<string, string> = {
  'aegis-connection': aegisConnection,
  'argus-eyes': argusEyes,
  'themis-scales': themisScales,
  'loki-knot': lokiKnot,
  'vulcan-anvil': vulcanAnvil,
  'heimdall-eye': heimdallEye,
  'janus-duality': janusDuality,
  'wedjat-eye': wedjatEye,
};

function radians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

function nodeStatusLabel(status: TopologyAgentNode['runtime']['status']): string {
  return status === 'planned' ? 'BASELINE' : status.toUpperCase();
}

function normalizeProductServiceCode(value: string): string {
  return value.replace(/\s/g, '').toLowerCase();
}

function StarGlyph({ symbol, color, size, lit }: { symbol: string; color: string; size: number; lit: boolean }) {
  const stroke = color;
  const common = {
    fill: 'none',
    stroke,
    strokeWidth: Math.max(1.4, size * 0.065),
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  const dot = (x: number, y: number, r = size * 0.07) => <circle cx={x} cy={y} r={r} fill={stroke} />;
  const half = size / 2;

  if (symbol === 'tool') return <g><path d={`M ${-half * 0.32} ${-half * 0.35} L ${half * 0.27} ${half * 0.25} M ${half * 0.32} ${-half * 0.33} L ${-half * 0.28} ${half * 0.27}`} {...common} />{dot(-half * 0.32, -half * 0.35, size * 0.055)}{dot(half * 0.32, -half * 0.33, size * 0.055)}</g>;
  if (symbol === 'api') return <g><path d={`M ${-half * 0.4} 0 H ${half * 0.4} M ${-half * 0.1} ${-half * 0.25} L ${-half * 0.4} 0 L ${-half * 0.1} ${half * 0.25} M ${half * 0.1} ${-half * 0.25} L ${half * 0.4} 0 L ${half * 0.1} ${half * 0.25}`} {...common} /></g>;
  return <g><ellipse cx="0" cy={-half * 0.22} rx={half * 0.32} ry={half * 0.13} {...common} /><path d={`M ${-half * 0.32} ${-half * 0.22} V ${half * 0.25} Q 0 ${half * 0.47} ${half * 0.32} ${half * 0.25} V ${-half * 0.22}`} {...common} /></g>;
}

function starTwinkle(id: string) {
  const hash = [...id].reduce((value, character) => ((value * 31) + character.charCodeAt(0)) >>> 0, 7);
  return {
    duration: `${1.42 + (hash % 17) / 100}s`,
    delay: `${(hash % 220) / 1000}s`,
  };
}

function selectTwinklingStars(starIds: string[], activeStarIds: Set<string>): string[] {
  const batchSize = Math.max(1, Math.round(starIds.length * TWINKLE_BATCH_RATIO));
  const candidates = starIds.filter((id) => !activeStarIds.has(id));
  const pool = candidates.length >= batchSize ? candidates : starIds;
  const shuffled = [...pool];

  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }

  return shuffled.slice(0, batchSize);
}

function splitTwinkleWaves(starIds: string[]): [string[], string[]] {
  const firstWaveSize = Math.ceil(starIds.length / 2);
  return [starIds.slice(0, firstWaveSize), starIds.slice(firstWaveSize)];
}

function buildRenderNodes(topology: StarmappingTopology): RenderNode[] {
  const center: RenderNode = {
    id: topology.center.id,
    kind: 'center',
    x: CANVAS.centerX,
    y: CANVAS.centerY,
    r: 44,
    color: 'var(--aegis-starmap-accent)',
    label: 'Aegis Core',
    symbol: topology.center.symbol,
    name: topology.center.name,
    detail: topology.center.description,
  };
  const nodes: RenderNode[] = [center];

  for (const agent of topology.agents) {
    const angle = radians(agent.layout.angle_degrees);
    const x = CANVAS.centerX + Math.cos(angle) * CANVAS.agentRadius;
    const y = CANVAS.centerY + Math.sin(angle) * CANVAS.agentRadius;
    const color = AGENT_COLORS[agent.id] || 'var(--aegis-starmap-object)';
    nodes.push({
      id: agent.id,
      kind: 'agent',
      x,
      y,
      r: 29,
      color,
      label: agent.business_domain,
      symbol: agent.symbol,
      agent,
      name: agent.display_name,
      detail: agent.business_fit,
    });

    for (const [index, star] of agent.star_nodes.entries()) {
      const clusterOffset = ((index % 6) - 2.5) * 12;
      const starAngle = angle + radians(clusterOffset);
      const starRadius = 82 + Math.floor(index / 6) * 25;
      nodes.push({
        id: star.id,
        parentId: agent.id,
        kind: 'star',
        x: x + Math.cos(starAngle) * starRadius,
        y: y + Math.sin(starAngle) * starRadius,
        r: index % 3 === 0 ? 6 : 4.5,
        color: STAR_COLORS[star.kind],
        label: star.name,
        symbol: star.kind,
        starKind: star.kind,
        name: star.name,
        detail: star.purpose,
        integration: star.integration,
        required: star.required,
      });
    }
  }
  return nodes;
}

interface AgentEntryState {
  code: string;
  status: string;
}

function NodeInsight({ node, agentEntryState }: { node: RenderNode; agentEntryState?: AgentEntryState }) {
  if (node.kind === 'center') {
    return <><p className="text-[10px] font-mono tracking-[0.18em] text-cyan-300">CENTER · ORCHESTRATION CORE</p><h4 className="mt-1 text-base font-semibold text-slate-100">{node.name}</h4><p className="mt-1.5 max-w-xl text-[11px] leading-relaxed text-slate-400">{node.detail}</p><p className="mt-2 text-[10px] font-mono text-slate-500">COORDINATING 7 DOMAIN AGENTS · 84 REQUIRED CAPABILITIES</p></>;
  }
  if (node.kind === 'agent' && node.agent) {
    return <><div className="flex flex-wrap items-center gap-2"><p className="text-[10px] font-mono tracking-[0.18em] text-cyan-300">{node.agent.business_domain} · AGENT RING</p><span className="rounded border border-cyan-900/60 bg-cyan-950/30 px-1.5 py-0.5 text-[9px] font-mono text-cyan-200">{nodeStatusLabel(node.agent.runtime.status)}</span></div><h4 className="mt-1 text-base font-semibold text-slate-100">{node.name} <span className="text-xs font-normal text-slate-500">{node.agent.marketing_name}</span></h4><p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">{node.detail}</p><p className="mt-2 text-[10px] font-mono text-slate-500">{node.agent.cultural_origin} · {node.agent.star_nodes.length} STARS · {node.agent.runtime.source}</p><p className="mt-2 text-[10px] font-mono text-slate-400">PRODUCT SERVICE · {agentEntryState?.code || node.agent.product_service_code}</p><p className={`mt-1 text-[10px] font-mono ${agentEntryState?.status === 'ENTRY READY' ? 'text-emerald-300' : 'text-amber-300/90'}`}>{agentEntryState?.status || 'ENTRY UNAVAILABLE'}</p></>;
  }
  return <><p className="text-[10px] font-mono tracking-[0.18em] text-cyan-300">{node.starKind?.toUpperCase()} · STAR FIELD</p><h4 className="mt-1 text-base font-semibold text-slate-100">{node.name}</h4><p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">{node.detail}</p><p className="mt-2 text-[10px] font-mono text-slate-500">{node.integration} · {node.required ? 'REQUIRED' : 'OPTIONAL'}</p></>;
}

export default function StarmappingTopology({ topology, error, appEntries, appEntriesLoading, appEntriesError, appEntriesLoaded }: StarmappingTopologyProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ id: string; x: number; y: number } | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const nodes = useMemo(() => topology ? buildRenderNodes(topology) : [], [topology]);
  const starNodeIds = useMemo(() => nodes.filter((node) => node.kind === 'star').map((node) => node.id), [nodes]);
  const [twinklingStarIds, setTwinklingStarIds] = useState<Set<string>>(() => new Set());
  const twinklingStarIdsRef = useRef<Set<string>>(new Set());
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const activeNodeId = hoveredNodeId;
  const activeNode = activeNodeId ? nodeById.get(activeNodeId) : undefined;
  const appEntryByCode = useMemo(
    () => new Map(appEntries.map((entry) => [normalizeProductServiceCode(entry.product_service_code), entry])),
    [appEntries],
  );
  const litIds = useMemo(() => {
    const lit = new Set<string>();
    if (!activeNode || !topology) return lit;
    lit.add(activeNode.id);
    if (activeNode.kind === 'agent') {
      lit.add(topology.center.id);
      activeNode.agent?.star_nodes.forEach((star) => lit.add(star.id));
    } else if (activeNode.kind === 'star' && activeNode.parentId) {
      lit.add(topology.center.id);
      lit.add(activeNode.parentId);
    }
    return lit;
  }, [activeNode, topology]);

  useEffect(() => {
    const syncFullscreenState = () => setIsFullscreen(Boolean(containerRef.current && document.fullscreenElement === containerRef.current));
    document.addEventListener('fullscreenchange', syncFullscreenState);
    syncFullscreenState();
    return () => document.removeEventListener('fullscreenchange', syncFullscreenState);
  }, []);

  useEffect(() => {
    if (starNodeIds.length === 0) {
      twinklingStarIdsRef.current = new Set();
      setTwinklingStarIds(new Set());
      return undefined;
    }

    const timeoutIds = new Set<number>();
    const schedule = (callback: () => void, delay: number) => {
      const timeoutId = window.setTimeout(() => {
        timeoutIds.delete(timeoutId);
        callback();
      }, delay);
      timeoutIds.add(timeoutId);
    };
    const publishActiveStars = () => setTwinklingStarIds(new Set(twinklingStarIdsRef.current));
    const activateWave = (starIds: string[]) => {
      if (starIds.length === 0) return;
      starIds.forEach((id) => twinklingStarIdsRef.current.add(id));
      publishActiveStars();
      schedule(() => {
        starIds.forEach((id) => twinklingStarIdsRef.current.delete(id));
        publishActiveStars();
      }, TWINKLE_WAVE_LIFETIME_MS);
    };
    const selectNextBatch = () => {
      const [firstWave, secondWave] = splitTwinkleWaves(selectTwinklingStars(starNodeIds, twinklingStarIdsRef.current));
      activateWave(firstWave);
      schedule(() => activateWave(secondWave), TWINKLE_WAVE_STAGGER_MS);
    };

    twinklingStarIdsRef.current = new Set();
    selectNextBatch();
    const interval = window.setInterval(selectNextBatch, TWINKLE_INTERVAL_MS);
    return () => {
      window.clearInterval(interval);
      timeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
      twinklingStarIdsRef.current = new Set();
    };
  }, [starNodeIds]);

  const showTooltip = (id: string, event: PointerEvent<SVGGElement>) => {
    const bounds = containerRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const width = 320;
    const height = 156;
    const pointerX = Number.isFinite(event.clientX) ? event.clientX : bounds.left + (bounds.width / 2);
    const pointerY = Number.isFinite(event.clientY) ? event.clientY : bounds.top + (bounds.height / 2);
    const x = Math.max(12, Math.min(pointerX - bounds.left + 16, bounds.width - width - 12));
    const y = Math.max(48, Math.min(pointerY - bounds.top + 16, bounds.height - height - 12));
    setHoveredNodeId(id);
    setTooltip({ id, x, y });
  };

  const hideTooltip = () => {
    setHoveredNodeId(null);
    setTooltip(null);
  };

  const focusNode = (id: string) => {
    setHoveredNodeId(id);
    setTooltip({ id, x: 20, y: 72 });
  };

  const activateNode = (id: string) => {
    focusNode(id);
    const node = nodeById.get(id);
    if (node?.kind !== 'agent' || !node.agent || appEntriesLoading || appEntriesError || !appEntriesLoaded) {
      return;
    }

    const entryUrl = appEntryByCode.get(normalizeProductServiceCode(node.agent.product_service_code))?.entry_url?.trim();
    if (entryUrl) {
      window.open(entryUrl, '_blank', 'noopener,noreferrer');
    }
  };

  const toggleFullscreen = async () => {
    if (isFullscreen || document.fullscreenElement) {
      await document.exitFullscreen();
      setIsFullscreen(false);
      return;
    }
    await containerRef.current?.requestFullscreen();
  };

  const handleNodeKeyDown = (event: KeyboardEvent<SVGGElement>, id: string) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      activateNode(id);
    }
  };

  const agentEntryState = activeNode?.kind === 'agent' && activeNode.agent
    ? {
      code: activeNode.agent.product_service_code,
      status: appEntriesLoading
        ? 'ENTRY UNAVAILABLE · DIRECTORY LOADING'
        : appEntriesError
          ? 'ENTRY UNAVAILABLE · DIRECTORY ERROR'
          : !appEntriesLoaded
            ? 'ENTRY UNAVAILABLE'
            : appEntryByCode.get(normalizeProductServiceCode(activeNode.agent.product_service_code))?.entry_url?.trim()
              ? 'ENTRY READY'
              : 'ENTRY UNAVAILABLE',
    }
    : undefined;

  if (!topology) {
    return <div className="aegis-starmap flex min-h-0 flex-1 items-center justify-center px-8 text-center"><div><p className="text-xs font-mono tracking-[0.2em] text-cyan-400">STARMAPPING UNAVAILABLE</p><p className="mt-3 max-w-sm text-xs leading-relaxed text-slate-500">{error || 'The three-layer topology is loading from the Aegis API.'}</p></div></div>;
  }

  return (
    <div ref={containerRef} className={`aegis-starmap relative min-h-0 flex-1 overflow-hidden ${isFullscreen ? 'h-screen w-screen' : ''}`}>
      <StarmappingStarfieldCanvas isFullscreen={isFullscreen} />
      <div className="aegis-starmap__veil absolute inset-0" />
      <div className="aegis-starmap__atmosphere absolute inset-0" />
      <svg className="relative h-full min-h-0 w-full" viewBox={`0 0 ${CANVAS.width} ${CANVAS.height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="Aegis three-layer orchestration topology">
        <defs>
          <radialGradient id="star-map-core" cx="50%" cy="50%" r="50%"><stop offset="0%" stopColor="var(--aegis-starmap-accent)" stopOpacity="0.4" /><stop offset="100%" stopColor="var(--aegis-starmap-bg)" stopOpacity="0" /></radialGradient>
          <filter id="star-map-glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <pattern id="star-map-hex" width="36" height="31" patternUnits="userSpaceOnUse"><path d="M9 1h18l9 15-9 14H9L0 16z" fill="none" stroke="var(--aegis-starmap-border)" strokeOpacity="0.6" strokeWidth="0.7" /></pattern>
        </defs>
        <rect width={CANVAS.width} height={CANVAS.height} fill="url(#star-map-core)" />
        <path d="M 16 680 Q 500 -114 984 680" fill="none" stroke="var(--aegis-starmap-object)" strokeOpacity="0.28" strokeWidth="1.25" />
        <path d="M 66 674 Q 500 -58 934 674" fill="none" stroke="var(--aegis-starmap-accent)" strokeOpacity="0.28" strokeWidth="1" strokeDasharray="4 9" />
        <path d="M 16 680 Q 500 -114 984 680 L 984 700 L 16 700 Z" fill="url(#star-map-hex)" opacity="0.38" />
        <circle cx={CANVAS.centerX} cy={CANVAS.centerY} r={CANVAS.agentRadius} fill="none" stroke="var(--aegis-starmap-object)" strokeOpacity="0.65" strokeWidth="1" />
        <circle cx={CANVAS.centerX} cy={CANVAS.centerY} r="70" fill="none" stroke="var(--aegis-starmap-accent)" strokeOpacity="0.55" strokeWidth="1" strokeDasharray="3 7" />

        {topology.edges.map((edge, index) => {
          const source = nodeById.get(edge.source);
          const target = nodeById.get(edge.target);
          if (!source || !target) return null;
          const active = litIds.has(edge.source) && litIds.has(edge.target);
          const showFlow = edge.mode === 'orchestrates' || active;
          const flowPath = `M ${source.x} ${source.y} L ${target.x} ${target.y}`;
          return (
            <g key={`${edge.source}-${edge.target}`}>
              <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke={active ? 'var(--aegis-starmap-focus)' : 'var(--aegis-starmap-border)'} strokeOpacity={active ? 0.96 : edge.mode === 'requires' ? 0.34 : 0.58} strokeWidth={active ? 1.5 : 0.7} strokeDasharray={edge.mode === 'requires' ? '2 5' : undefined} />
              {showFlow ? <circle data-testid="topology-flow" r={active ? 2.25 : 1.35} fill={active ? 'var(--aegis-starmap-focus)' : 'var(--aegis-starmap-accent)'} fillOpacity={active ? 1 : 0.78} filter="url(#star-map-glow)"><animateMotion path={flowPath} dur={`${active ? 1.5 : 2.8 + (index % 3) * 0.25}s`} begin={`-${(index % 7) * 0.36}s`} repeatCount="indefinite" /></circle> : null}
            </g>
          );
        })}

        {nodes.map((node) => {
          const lit = litIds.has(node.id);
          const isActive = activeNode?.id === node.id;
          const opacity = node.kind === 'star' ? (lit ? 1 : 0.64) : 1;
          const twinkle = node.kind === 'star' && twinklingStarIds.has(node.id) ? starTwinkle(node.id) : null;
          const symbolAsset = node.kind === 'star' ? undefined : SYMBOL_ASSETS[node.symbol];
          const agentPhase = node.agent?.layout.ring_position ?? 0;
          return (
            <g
              key={node.id}
              transform={`translate(${node.x} ${node.y})`}
              role="button"
              tabIndex={0}
              aria-label={`${node.name}: ${node.detail}`}
              onPointerEnter={(event) => showTooltip(node.id, event)}
              onPointerMove={(event) => showTooltip(node.id, event)}
              onPointerLeave={hideTooltip}
              onFocus={() => focusNode(node.id)}
              onBlur={hideTooltip}
              onClick={() => activateNode(node.id)}
              onKeyDown={(event) => handleNodeKeyDown(event, node.id)}
              className="cursor-pointer outline-none"
              opacity={opacity}
            >
              <g className={node.kind === 'agent' ? 'starmapping-agent-assembly' : undefined} style={node.kind === 'agent' ? { animationDelay: `${-(agentPhase * 0.82)}s` } : undefined}>
                {node.kind === 'center' ? <>
                  <circle data-testid="topology-core-halo" className="starmapping-core-halo" r={node.r + 14} fill="none" stroke={node.color} strokeOpacity="0.22" strokeWidth="1.3" filter="url(#star-map-glow)" />
                  <circle data-testid="topology-core-orbit" className="starmapping-core-orbit" r={node.r + 20} fill="none" stroke={node.color} strokeOpacity="0.52" strokeWidth="0.9" strokeDasharray="3 10" />
                </> : null}
                {node.kind === 'agent' ? <>
                  <circle data-testid={`topology-agent-orbit-${node.id}`} className="starmapping-agent-orbit starmapping-agent-orbit--outer" r={node.r + 7} fill="none" stroke={node.color} strokeOpacity="0.34" strokeWidth="0.75" strokeDasharray="3 11" style={{ animationDelay: `${-(agentPhase * 1.5)}s` }} />
                  <circle className="starmapping-agent-orbit starmapping-agent-orbit--inner" r={node.r + 3.5} fill="none" stroke={node.color} strokeOpacity="0.23" strokeWidth="0.65" strokeDasharray="1.5 9" style={{ animationDelay: `${-(agentPhase * 0.9)}s` }} />
                  <circle data-testid={`topology-agent-scout-${node.id}`} className="starmapping-agent-scout" r={node.r + 8.5} fill="none" stroke={node.color} strokeOpacity="0.82" strokeWidth="1.35" strokeDasharray="1.5 34" style={{ animationDelay: `${-(agentPhase * 1.2)}s` }} filter="url(#star-map-glow)" />
                </> : null}
                {node.kind !== 'star' ? <circle r={node.r + (isActive ? 10 : 6)} fill="none" stroke={node.color} strokeOpacity={lit ? 0.55 : 0.22} strokeWidth={lit ? 1.35 : 0.7} /> : null}
                {node.kind === 'star' && twinkle ? <circle data-testid="topology-twinkle" data-star-id={node.id} className="starmapping-star-twinkle" r={node.r + 4} fill={node.color} opacity="0.08" filter="url(#star-map-glow)" style={{ animationName: 'starmapping-star-twinkle', animationDuration: twinkle.duration, animationDelay: twinkle.delay, animationTimingFunction: 'cubic-bezier(0.22, 1, 0.36, 1)', animationIterationCount: 1, animationFillMode: 'both' }} /> : null}
                <circle r={node.r} fill="var(--aegis-starmap-surface)" fillOpacity={node.kind === 'star' ? 0.96 : 0.9} stroke={node.color} strokeOpacity={lit ? 1 : 0.62} strokeWidth={isActive ? 1.7 : node.kind === 'star' ? 0.8 : 1.1} filter={lit ? 'url(#star-map-glow)' : undefined} />
                {symbolAsset ? <image data-testid={`topology-symbol-${node.id}`} href={symbolAsset} x={node.kind === 'center' ? -20 : -14} y={node.kind === 'center' ? -20 : -14} width={node.kind === 'center' ? 40 : 28} height={node.kind === 'center' ? 40 : 28} opacity={lit || node.kind !== 'star' ? 1 : 0.74} filter={lit ? 'url(#star-map-glow)' : undefined} /> : <StarGlyph symbol={node.symbol} color={node.color} size={8} lit={lit} />}
                {node.kind === 'agent' ? <text y={node.r + 17} textAnchor="middle" fill={lit ? 'var(--aegis-starmap-text)' : 'var(--aegis-starmap-muted)'} fontSize="9.5" fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace" letterSpacing="0.8">{node.label}</text> : null}
              </g>
            </g>
          );
        })}
      </svg>

      {tooltip && activeNode?.id === tooltip.id ? <div role="status" className="aegis-starmap__tooltip pointer-events-none absolute z-20 w-80 rounded-lg border p-3 backdrop-blur-md" style={{ left: tooltip.x, top: tooltip.y }}><NodeInsight node={activeNode} agentEntryState={agentEntryState} /></div> : null}
      <div className="pointer-events-none absolute right-14 top-4 flex gap-3 text-[9px] font-mono tracking-wide text-slate-500"><span><i className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-cyan-300" />CORE</span><span><i className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-teal-300" />AGENT RING</span><span><i className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-indigo-300" />STAR FIELD</span></div>
      <button type="button" aria-label={isFullscreen ? 'Exit topology fullscreen' : 'Enter topology fullscreen'} title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen topology'} onClick={() => void toggleFullscreen()} className="aegis-starmap__control absolute right-4 top-3 z-30 rounded-md border p-2 shadow-lg backdrop-blur transition">
        {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
      </button>
    </div>
  );
}
