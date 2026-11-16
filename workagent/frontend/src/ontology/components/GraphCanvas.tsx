import { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { statusColor } from '../design/tokens';

interface GraphEdge { id: string; source: string; target: string; type?: string; label?: string; kind?: string; cross_domain?: boolean }

type ColorMode = 'domain' | 'status';

// L3 object shapes — deliberately NON-circle & NON-square so all 3 layers differ:
//   domain = ellipse(圆), subcap = round-rectangle(方), objects = these:
const OBJ_SHAPE: Record<string, string> = {
  system: 'round-diamond',    // 对接系统 ◇
  data_source: 'round-tag',   // 数据源 ▭tag
  tool: 'round-hexagon',      // 工具 ⬡
  action: 'round-triangle',   // 动作 △
};
const LAYER_COLOR: Record<number, string> = {
  1: '#7E6BFF',  // L1 / Domain
  2: '#39D1FF',  // L2 / SubCapability
  3: '#41D6A4',  // L3 / Object
};

export function GraphCanvas({
  nodes,
  edges = [],
  onSelect,
  selectedId,
  colorMode = 'domain',
  height = 560,
  showEdgeLabels = true,
  cluster = true,
}: {
  nodes: any[];
  edges?: GraphEdge[];
  onSelect: (n: any) => void;
  selectedId?: string | null;
  colorMode?: ColorMode;
  height?: number;
  showEdgeLabels?: boolean;
  cluster?: boolean;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [ready, setReady] = useState(false);

  const nodeIds = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);
  const layerOf = (n: any): number =>
    n.layer || (n.type === 'Domain' ? 1 : (n.type === 'Object' || n.object_type) ? 3 : 2);

  const paint = (n: any) => {
    if (colorMode === 'status' && n.status) return statusColor(n.status);
    return LAYER_COLOR[layerOf(n)] || '#7C8DA6';
  };
  const shapeOf = (n: any): string => {
    const l = layerOf(n);
    if (l === 1) return 'ellipse';           // 域 = 圆形
    if (l === 2) return 'round-rectangle';   // 二级功能 = 方形
    if ((n.object_type || '') === 'data_source') return 'round-tag';
    return OBJ_SHAPE[n.object_type || ''] || 'round-pentagon'; // L3 = 类型图形（非圆非方）
  };

  const elements = useMemo(() => {
    const nodeEls = nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.label || n.name_zh || n.name_en || n.id,
        color: paint(n),
        weight: Number(n.importance_weight ?? n.subtotal ?? 2.2),
        layer: layerOf(n),
        domain: n.domain || n.id,
        shape: shapeOf(n),
        raw: n,
      },
    }));
    const edgeEls = edges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map((e) => ({
        data: {
          id: e.id, source: e.source, target: e.target,
          label: e.label || e.type || '', kind: e.kind || 'semantic',
          cross: e.cross_domain ? 'yes' : 'no',
        },
      }));
    return [...nodeEls, ...edgeEls];
  }, [nodes, edges, nodeIds, colorMode]);
  // ── cluster-by-domain layout (phyllotaxis disk per domain) + halo geometry ──
  const clusterGeom = useMemo(() => {
    if (!cluster) return null;
    const domains = nodes.filter((n) => layerOf(n) === 1);
    if (domains.length === 0) return null;
    const single = domains.length === 1;

    const subDom: Record<string, string> = {};
    nodes.forEach((n) => { if (layerOf(n) === 2) subDom[n.id] = n.domain; });
    const objDom: Record<string, string> = {};
    edges.forEach((e: any) => {
      if (e.type === 'requires' || e.kind === 'hierarchy') {
        const s = subDom[e.source]; const isObj = nodes.find((n) => n.id === e.target && layerOf(n) === 3);
        if (s && isObj && !objDom[e.target]) objDom[e.target] = s;
      }
    });
    const members: Record<string, any[]> = {};
    domains.forEach((d) => (members[d.id] = []));
    nodes.forEach((n) => {
      const l = layerOf(n);
      if (l === 2 && members[n.domain]) members[n.domain].push(n);
      else if (l === 3) { const dm = objDom[n.id] || domains[0].id; (members[dm] ||= []).push(n); }
    });

    const pos: Record<string, { x: number; y: number }> = {};
    const halos: { id: string; x: number; y: number; r: number; color: string }[] = [];
    const GOLDEN = Math.PI * (3 - Math.sqrt(5));
    const ringR = single ? 0 : 360 + domains.length * 46;
    const cx = 0, cy = 0;
    domains.forEach((d, i) => {
      const ang = (i / domains.length) * Math.PI * 2 - Math.PI / 2;
      const dcx = single ? cx : cx + ringR * Math.cos(ang);
      const dcy = single ? cy : cy + ringR * Math.sin(ang);
      pos[d.id] = { x: dcx, y: dcy };
      const mem = members[d.id] || [];
      const spacing = single ? 62 : 46;
      let maxR = 0;
      mem.forEach((m, k) => {
        const r = spacing * Math.sqrt(k + 1);
        const a = (k + 1) * GOLDEN;
        pos[m.id] = { x: dcx + r * Math.cos(a), y: dcy + r * Math.sin(a) };
        if (r > maxR) maxR = r;
      });
      halos.push({ id: `halo:${d.id}`, x: dcx, y: dcy, r: (maxR + 60) * 2, color: LAYER_COLOR[1] });
    });
    return { pos, halos };
  }, [nodes, edges, cluster]);
  const clusterPositions = clusterGeom?.pos || null;

  useEffect(() => {
    if (!ref.current) return;
    setReady(false);
    cyRef.current?.destroy();

    // halo background nodes (one translucent disk per domain cluster)
    const haloEls = (clusterGeom?.halos || []).map((h) => ({
      data: { id: h.id, halo: 'yes', color: h.color, hr: h.r }, selectable: false, grabbable: false,
    }));
    const allEls = [...haloEls, ...elements];
    const haloPos: Record<string, { x: number; y: number }> = {};
    (clusterGeom?.halos || []).forEach((h) => { haloPos[h.id] = { x: h.x, y: h.y }; });

    // Preset cluster layout (phyllotaxis per domain) when available; else force-directed.
    const layout: any = clusterPositions
      ? { name: 'preset', positions: (n: any) => clusterPositions[n.id()] || haloPos[n.id()], fit: true, padding: 70, animate: true, animationDuration: 700 }
      : {
          name: 'cose', fit: true, padding: 60, animate: true, animationDuration: 700,
          nodeDimensionsIncludeLabels: true, nodeRepulsion: () => 200000, nodeOverlap: 80,
          idealEdgeLength: (e: any) => (e.data('kind') === 'hierarchy' ? 110 : 220),
          edgeElasticity: () => 100, gravity: 8, componentSpacing: 200,
          numIter: 2800, coolingFactor: 0.95, initialTemp: 250, randomize: true,
        };

    const cy = cytoscape({
      container: ref.current,
      elements: allEls,
      style: [
        // cluster halo — translucent domain-colored disk behind each cluster
        {
          selector: 'node[halo = "yes"]',
          style: {
            shape: 'ellipse', 'background-color': 'data(color)', 'background-opacity': 0.14,
            'border-width': 2.5, 'border-color': 'data(color)', 'border-opacity': 0.5,
            width: 'data(hr)', height: 'data(hr)', label: '', 'z-index': 0, events: 'no' as any,
          } as any,
        },
        {
          selector: 'node[halo != "yes"]',
          style: {
            'background-color': 'data(color)', 'background-opacity': 0.92, shape: 'data(shape)' as any,
            label: 'data(label)', color: '#E4EEFF', 'font-family': 'Inter, sans-serif', 'font-size': 10,
            'font-weight': 500, 'text-wrap': 'wrap', 'text-max-width': '92px', 'text-valign': 'bottom',
            'text-margin-y': 4, 'text-outline-color': '#04060C', 'text-outline-width': 3, 'min-zoomed-font-size': 7,
            width: (e: any) => `${Math.min(42, Math.max(19, Number(e.data('weight')) * 7))}`,
            height: (e: any) => `${Math.min(42, Math.max(19, Number(e.data('weight')) * 7))}`,
            'border-width': 2, 'border-color': 'data(color)', 'border-opacity': 0.5,
            'transition-property': 'opacity, border-width, border-opacity', 'transition-duration': 160,
          } as any,
        },
        // ① 域 = 圆，缩小，光晕外环
        {
          selector: 'node[layer = 1]',
          style: {
            shape: 'ellipse', 'font-family': 'Orbitron, Inter, sans-serif', 'font-size': 11, 'font-weight': 700,
            'text-valign': 'center', 'text-margin-y': 0, 'text-max-width': '70px', color: '#F2F7FF',
            width: (e: any) => `${Math.min(80, 46 + Number(e.data('weight')) * 2.2)}`,
            height: (e: any) => `${Math.min(80, 46 + Number(e.data('weight')) * 2.2)}`,
            'border-width': 3, 'border-opacity': 1, 'background-opacity': 0.24, 'z-index': 20,
            'text-outline-width': 3,
          } as any,
        },
        // ② 二级功能 = 方形，放大
        {
          selector: 'node[layer = 2]',
          style: {
            shape: 'round-rectangle', 'background-opacity': 0.92, 'border-width': 2, 'border-opacity': 0.85, 'font-size': 11,
            width: (e: any) => `${Math.min(46, 30 + Number(e.data('weight')) * 3)}`,
            height: (e: any) => `${Math.min(38, 26 + Number(e.data('weight')) * 2.4)}`,
          } as any,
        },
        // ③ 三级对象 = 类型图形，放大
        {
          selector: 'node[layer = 3]',
          style: {
            'background-opacity': 0.85,
            'font-size': 9.5,
            'text-max-width': '84px',
            width: (e: any) => (e.data('raw')?.object_type === 'data_source' ? 31 : 27),
            height: (e: any) => (e.data('raw')?.object_type === 'data_source' ? 29 : 27),
            'border-width': (e: any) => (e.data('raw')?.object_type === 'data_source' ? 2.3 : 1.5),
            'border-opacity': 0.82,
            'border-color': (e: any) => (e.data('raw')?.object_type === 'data_source' ? '#9AF0D6' : e.data('color')),
            'shadow-blur': (e: any) => (e.data('raw')?.object_type === 'data_source' ? 16 : 0),
            'shadow-color': (e: any) => (e.data('raw')?.object_type === 'data_source' ? '#3FB9A0' : '#000000'),
            'shadow-opacity': (e: any) => (e.data('raw')?.object_type === 'data_source' ? 0.28 : 0),
          } as any,
        },
        { selector: 'node:selected', style: { 'border-width': 6, 'border-opacity': 1, 'border-color': '#7FE9FF' } },
        { selector: 'node.hl', style: { 'border-width': 4, 'border-opacity': 1 } },
        { selector: 'node.pinned', style: { 'border-width': 4, 'border-opacity': 1, 'border-color': '#7FE9FF' } },
        { selector: 'edge.pinned', style: { opacity: 1, width: 3, 'line-color': '#7FE9FF', 'target-arrow-color': '#7FE9FF' } as any },
        { selector: 'node.dim', style: { opacity: 0.12 } },
        { selector: 'edge.dim', style: { opacity: 0.06 } },
        {
          selector: 'edge',
          style: { width: 1, 'line-color': '#243449', 'curve-style': 'bezier', 'target-arrow-shape': 'none', opacity: 0.28,
            'transition-property': 'opacity, line-color, width', 'transition-duration': 200 } as any,
        },
        // 层级/域内结构（contains/requires）— 柔和实线，稳定常显（不做自动半隐）
        {
          selector: 'edge[kind = "hierarchy"]',
          style: {
            width: 1.6, 'line-color': '#5C7BA8', 'line-style': 'solid',
            'target-arrow-shape': 'none', opacity: 0.55,
          } as any,
        },
        // 域内 semantic（同域语义关系）— 柔和实线，稳定常显
        {
          selector: 'edge[kind = "semantic"][cross = "no"]',
          style: {
            width: 1.8, 'line-color': '#6E93C8', 'line-style': 'solid',
            'target-arrow-shape': 'triangle', 'target-arrow-color': '#6E93C8', 'arrow-scale': 0.85, opacity: 0.7,
            label: showEdgeLabels ? 'data(label)' : '', 'font-size': 10, 'font-family': 'Inter, sans-serif', color: '#C3D6F2',
            'text-background-color': '#060A12', 'text-background-opacity': 0.85, 'text-background-padding': '3px', 'text-rotation': 'autorotate',
          } as any,
        },
        // 跨域 semantic — 虚线，流动，青色（默认淡，hover 点亮，避免总览杂乱）
        {
          selector: 'edge[kind = "semantic"][cross = "yes"]',
          style: {
            width: 1.8, 'line-color': '#6FD3FF', 'line-style': 'dashed', 'line-dash-pattern': [8, 6],
            'target-arrow-shape': 'triangle', 'target-arrow-color': '#6FD3FF', 'arrow-scale': 0.85, opacity: 0.2, color: '#AEDBFF',
            label: showEdgeLabels ? 'data(label)' : '', 'font-size': 10, 'font-family': 'Inter, sans-serif',
            'text-background-color': '#060A12', 'text-background-opacity': 0.85, 'text-background-padding': '3px', 'text-rotation': 'autorotate',
          } as any,
        },
        {
          selector: 'edge.hl',
          style: {
            'line-color': '#7FE9FF', 'target-arrow-color': '#7FE9FF', 'target-arrow-shape': 'triangle', width: 3, opacity: 1,
            'line-style': 'dashed', 'line-dash-pattern': [6, 4], label: 'data(label)', 'font-size': 11, 'font-weight': 600, color: '#EAF2FF',
            'text-background-color': '#060A12', 'text-background-opacity': 0.9, 'text-background-padding': '3px', 'text-rotation': 'autorotate', 'z-index': 999,
          } as any,
        },
      ],
      layout, minZoom: 0.18, maxZoom: 2.6, wheelSensitivity: 0.2,
    });

    const pinSelection = (nodeId: string | null) => {
      cy.elements().removeClass('pinned');
      if (!nodeId) return;
      const n = cy.getElementById(nodeId);
      if (!n || !n.length) return;
      const nb = n.closedNeighborhood();
      nb.addClass('pinned');
    };

    cy.on('tap', 'node[halo != "yes"]', (evt) => {
      const raw = evt.target.data('raw');
      onSelect(raw);
      pinSelection(raw?.id || evt.target.id());
    });
    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        onSelect(null as any);
        pinSelection(null);
      }
    });
    cy.on('mouseover', 'node[halo != "yes"]', (evt) => {
      const nb = evt.target.closedNeighborhood();
      cy.elements('[halo != "yes"]').not('.pinned').addClass('dim');
      cy.edges().not('.pinned').addClass('dim');
      nb.removeClass('dim').addClass('hl');
    });
    cy.on('mouseout', 'node[halo != "yes"]', () => {
      cy.elements().removeClass('dim').removeClass('hl');
      // restore persistent selected highlight after hover ends
      const sel = cy.$('node.pinned');
      if (sel.length) {
        const anchor = sel[0];
        anchor.closedNeighborhood().addClass('pinned');
      }
    });
    cy.one('layoutstop', () => setReady(true));
    setTimeout(() => setReady(true), 1200);

    // always-on flowing links (marching-ants) — respects reduced-motion
    let offset = 0; let raf = 0;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const flow = () => {
      offset = (offset + 0.4) % 22;
      cy.edges('[kind = "semantic"][cross = "yes"]').style('line-dash-offset', -offset);
      cy.edges('.hl').style('line-dash-offset', -offset * 2.2);
      raf = requestAnimationFrame(flow);
    };
    if (!reduced) flow();

    cyRef.current = cy;
    return () => { cancelAnimationFrame(raf); cy.destroy(); };
  }, [elements, onSelect, colorMode, clusterPositions]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect().removeClass('pinned');
    if (selectedId) {
      const n = cy.getElementById(selectedId);
      if (n && n.length) {
        n.select();
        n.closedNeighborhood().addClass('pinned');
        cy.animate({ fit: { eles: n.closedNeighborhood(), padding: 110 }, duration: 320, easing: 'ease-out' });
      }
    }
  }, [selectedId]);

  return (
    <div className="relative w-full" style={{ height }}>
      <div
        ref={ref}
        className="h-full w-full rounded-2xl border overflow-hidden"
        style={{
          borderColor: 'var(--stroke-soft)',
          background:
            'radial-gradient(620px 420px at 28% 14%, rgba(56,225,255,0.07), transparent 55%), radial-gradient(620px 420px at 76% 82%, rgba(167,139,250,0.07), transparent 55%), #05070E',
        }}
      />
      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-[color:var(--ink-lo)] font-mono pointer-events-none">
          <span className="anim-pulse">compiling cluster layout…</span>
        </div>
      )}
    </div>
  );
}
