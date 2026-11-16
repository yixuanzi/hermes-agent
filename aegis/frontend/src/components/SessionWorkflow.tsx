import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Focus,
  Maximize2,
  Minimize2,
  RotateCcw,
  Workflow,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { Conversation, WorkflowGraphNode } from '../types';
import {
  compactWorkflowGraph,
  projectSessionWorkflow,
  WorkflowToolRunExpansion,
} from '../lib/sessionWorkflow';

interface SessionWorkflowProps {
  conversation?: Conversation;
  fullscreen: boolean;
  onFullscreenChange: (fullscreen: boolean) => void;
  onClose?: () => void;
  showHeader?: boolean;
}

interface ViewportTransform {
  x: number;
  y: number;
  scale: number;
}

const MIN_SCALE = 0.35;
const MAX_SCALE = 2.5;

const NODE_COLORS: Record<WorkflowGraphNode['kind'], { fill: string; stroke: string; glow: string }> = {
  root: { fill: 'var(--aegis-warning-text)', stroke: 'color-mix(in srgb, var(--aegis-warning-text) 62%, var(--aegis-text))', glow: 'color-mix(in srgb, var(--aegis-warning-text) 42%, transparent)' },
  input: { fill: 'var(--aegis-accent)', stroke: 'color-mix(in srgb, var(--aegis-accent) 62%, var(--aegis-text))', glow: 'color-mix(in srgb, var(--aegis-accent) 34%, transparent)' },
  delegate: { fill: 'var(--aegis-danger-text)', stroke: 'color-mix(in srgb, var(--aegis-danger-text) 62%, var(--aegis-text))', glow: 'color-mix(in srgb, var(--aegis-danger-text) 36%, transparent)' },
  tool: { fill: 'var(--aegis-ticker-object)', stroke: 'color-mix(in srgb, var(--aegis-ticker-object) 62%, var(--aegis-text))', glow: 'color-mix(in srgb, var(--aegis-ticker-object) 32%, transparent)' },
  'tool-group': { fill: 'var(--aegis-ticker-actor)', stroke: 'color-mix(in srgb, var(--aegis-ticker-actor) 62%, var(--aegis-text))', glow: 'color-mix(in srgb, var(--aegis-ticker-actor) 40%, transparent)' },
  end: { fill: 'var(--aegis-success-text)', stroke: 'color-mix(in srgb, var(--aegis-success-text) 62%, var(--aegis-text))', glow: 'color-mix(in srgb, var(--aegis-success-text) 30%, transparent)' },
};

const FAILED_COLORS = {
  fill: 'var(--aegis-danger-text)',
  stroke: 'color-mix(in srgb, var(--aegis-danger-text) 72%, var(--aegis-text))',
  glow: 'color-mix(in srgb, var(--aegis-danger-text) 35%, transparent)',
};

/** P0-3 径向渐变用的中心亮色（每种 kind 一档提亮） */
const NODE_GRADIENT_CENTER: Record<WorkflowGraphNode['kind'], string> = {
  root: 'color-mix(in srgb, var(--aegis-warning-text) 58%, var(--aegis-text))',
  input: 'color-mix(in srgb, var(--aegis-accent) 58%, var(--aegis-text))',
  delegate: 'color-mix(in srgb, var(--aegis-danger-text) 58%, var(--aegis-text))',
  tool: 'color-mix(in srgb, var(--aegis-ticker-object) 58%, var(--aegis-text))',
  'tool-group': 'color-mix(in srgb, var(--aegis-ticker-actor) 58%, var(--aegis-text))',
  end: 'color-mix(in srgb, var(--aegis-success-text) 58%, var(--aegis-text))',
};

const NODE_GRADIENT_IDS: Record<WorkflowGraphNode['kind'], string> = {
  root: 'workflow-grad-root',
  input: 'workflow-grad-input',
  delegate: 'workflow-grad-delegate',
  tool: 'workflow-grad-tool',
  'tool-group': 'workflow-grad-tool-group',
  end: 'workflow-grad-end',
};

const DETAIL_ACCENT: Record<WorkflowGraphNode['kind'], string> = {
  root: 'var(--aegis-warning-text)',
  input: 'var(--aegis-accent)',
  delegate: 'var(--aegis-danger-text)',
  tool: 'var(--aegis-ticker-object)',
  'tool-group': 'var(--aegis-ticker-actor)',
  end: 'var(--aegis-success-text)',
};

/** P2-2 相对时间格式化 */
function formatRelativeTime(timestamp?: number): string {
  if (!timestamp) {
    return '';
  }
  const deltaSeconds = Math.max(0, Date.now() / 1000 - timestamp);
  if (deltaSeconds < 5) {
    return 'just now';
  }
  if (deltaSeconds < 60) {
    return `${Math.floor(deltaSeconds)}s ago`;
  }
  if (deltaSeconds < 3600) {
    return `${Math.floor(deltaSeconds / 60)}m ago`;
  }
  if (deltaSeconds < 86400) {
    return `${Math.floor(deltaSeconds / 3600)}h ago`;
  }
  return `${Math.floor(deltaSeconds / 86400)}d ago`;
}

function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

function nodeRadius(node: WorkflowGraphNode, nodeCount: number): number {
  const density = nodeCount > 100 ? 0.72 : nodeCount > 45 ? 0.84 : 1;
  const base =
    node.kind === 'root'
      ? 25
      : node.kind === 'delegate'
        ? 17
        : node.kind === 'input'
          ? 14
          : node.kind === 'tool-group'
            ? 13
            : 11;
  return base * density;
}

function nodeHaloSize(node: WorkflowGraphNode, radius: number, nodeCount: number): number {
  const density = nodeCount > 100 ? 0.45 : nodeCount > 45 ? 0.7 : 1;
  const extension =
    node.kind === 'root'
      ? 18
      : node.kind === 'delegate' || node.kind === 'tool-group'
        ? 12
        : node.kind === 'input'
          ? 9
          : 7;
  return radius + extension * density;
}

function edgePath(from: WorkflowGraphNode, to: WorkflowGraphNode): string {
  const bend = Math.max(44, (to.x - from.x) * 0.48);
  return `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`;
}

function formatTimestamp(timestamp?: number): string {
  if (!timestamp) {
    return '—';
  }
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function isWorkflowOverlayTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest('[data-workflow-overlay]'));
}

export default function SessionWorkflow({
  conversation,
  fullscreen,
  onFullscreenChange,
  onClose,
  showHeader = true,
}: SessionWorkflowProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; originX: number; originY: number } | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [transform, setTransform] = useState<ViewportTransform>({ x: 36, y: 36, scale: 1 });
  const [userMovedViewport, setUserMovedViewport] = useState(false);
  const [revealedByRun, setRevealedByRun] = useState<WorkflowToolRunExpansion>({});
  const [hoveredNodeId, setHoveredNodeId] = useState<string>();
  /* P0-2 动画状态：出生的节点/边、完成回弹、失败抖动 */
  const [bornNodeIds, setBornNodeIds] = useState<ReadonlySet<string>>(new Set());
  const [bornEdgeIds, setBornEdgeIds] = useState<ReadonlySet<string>>(new Set());
  const [settledNodeIds, setSettledNodeIds] = useState<ReadonlySet<string>>(new Set());
  const [shookNodeIds, setShookNodeIds] = useState<ReadonlySet<string>>(new Set());
  /* P2-3 完成收束仪式 */
  const [completePingActive, setCompletePingActive] = useState(false);
  const prevGraphRef = useRef<{ nodeStatuses: Map<string, string>; edgeIds: Set<string>; conversationId?: string; status?: string }>({
    nodeStatuses: new Map(),
    edgeIds: new Set(),
  });
  const animTimersRef = useRef<number[]>([]);
  const legacyTraceAvailable = Boolean(
    conversation?.messages.some(
      (message) =>
        message.turnId ||
        message.kind === 'main-tools' ||
        message.kind === 'delegate-tools' ||
        (message.sender === 'user' && !message.clientMsgId),
    ) && !conversation?.workflowTraceVersion,
  );

  const logicalGraph = useMemo(
    () =>
      projectSessionWorkflow({
        conversationId: conversation?.id || 'empty',
        title: conversation?.title || 'Current Session',
        messages: conversation?.messages || [],
        trace: conversation?.workflowTrace || [],
        partial: legacyTraceAvailable,
      }),
    [conversation, legacyTraceAvailable],
  );
  const graph = useMemo(
    () => compactWorkflowGraph(logicalGraph, revealedByRun),
    [logicalGraph, revealedByRun],
  );
  const nodeById = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node])),
    [graph.nodes],
  );
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) : undefined;
  const highlightedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    let cursor = selectedNode;
    while (cursor) {
      ids.add(cursor.id);
      cursor = cursor.parentId ? nodeById.get(cursor.parentId) : undefined;
    }
    return ids;
  }, [nodeById, selectedNode]);

  /* P0-4 最新活动 running 节点（雷达 ping 定位） */
  const livestNode = useMemo(() => {
    let latest: WorkflowGraphNode | undefined;
    graph.nodes.forEach((node) => {
      if (node.status !== 'running' || !node.timestamp) {
        return;
      }
      if (!latest || (node.timestamp || 0) > (latest.timestamp || 0)) {
        latest = node;
      }
    });
    return latest;
  }, [graph.nodes]);

  /* P1-1 delegate 泳道领地：每个 delegate 子树的包围盒 */
  const delegateTerritories = useMemo(() => {
    const childrenById = new Map<string, WorkflowGraphNode[]>();
    graph.nodes.forEach((node) => {
      if (!node.parentId) {
        return;
      }
      const siblings = childrenById.get(node.parentId) || [];
      siblings.push(node);
      childrenById.set(node.parentId, siblings);
    });
    const territories: Array<{ id: string; agent: string; x: number; y: number; width: number; height: number }> = [];
    graph.nodes
      .filter((node) => node.kind === 'delegate')
      .forEach((delegateNode) => {
        const subtree: WorkflowGraphNode[] = [delegateNode];
        const queue = [delegateNode];
        while (queue.length) {
          const current = queue.pop() as WorkflowGraphNode;
          (childrenById.get(current.id) || []).forEach((child) => {
            subtree.push(child);
            queue.push(child);
          });
        }
        const padding = 42;
        const minX = Math.min(...subtree.map((node) => node.x)) - padding;
        const maxX = Math.max(...subtree.map((node) => node.x)) + padding + 96;
        const minY = Math.min(...subtree.map((node) => node.y)) - padding;
        const maxY = Math.max(...subtree.map((node) => node.y)) + padding;
        territories.push({
          id: delegateNode.id,
          agent: delegateNode.agent || delegateNode.label,
          x: minX,
          y: minY,
          width: maxX - minX,
          height: maxY - minY,
        });
      });
    return territories;
  }, [graph.nodes]);

  /* P0-2 / P2-3 图变化 diff：新节点出生、新边画出、状态转变回弹/抖动、完成收束 */
  useEffect(() => {
    const prev = prevGraphRef.current;
    const conversationChanged = prev.conversationId !== conversation?.id;
    const nextStatuses = new Map(graph.nodes.map((node) => [node.id, node.status]));
    const nextEdgeIds = new Set(graph.edges.map((edge) => edge.id));

    if (conversationChanged) {
      prevGraphRef.current = {
        nodeStatuses: nextStatuses,
        edgeIds: nextEdgeIds,
        conversationId: conversation?.id,
        status: logicalGraph.status,
      };
      setBornNodeIds(new Set());
      setBornEdgeIds(new Set());
      setSettledNodeIds(new Set());
      setShookNodeIds(new Set());
      setCompletePingActive(false);
      return;
    }

    const isFirstLayout = prev.nodeStatuses.size === 0;
    const born = new Set<string>();
    const settled = new Set<string>();
    const shook = new Set<string>();
    graph.nodes.forEach((node) => {
      const prevStatus = prev.nodeStatuses.get(node.id);
      if (!isFirstLayout && prevStatus === undefined) {
        born.add(node.id);
      } else if (prevStatus === 'running' && node.status === 'completed') {
        settled.add(node.id);
      } else if (prevStatus === 'running' && node.status !== 'running' && node.status !== 'completed') {
        shook.add(node.id);
      }
    });
    const bornEdges = new Set<string>();
    if (!isFirstLayout) {
      graph.edges.forEach((edge) => {
        if (!prev.edgeIds.has(edge.id)) {
          bornEdges.add(edge.id);
        }
      });
    }

    const becameComplete = prev.status === 'live' && logicalGraph.status === 'complete';
    if (born.size || settled.size || shook.size || bornEdges.size || becameComplete) {
      setBornNodeIds(born);
      setBornEdgeIds(bornEdges);
      setSettledNodeIds(settled);
      setShookNodeIds(shook);
      if (becameComplete) {
        setCompletePingActive(true);
      }
      animTimersRef.current.forEach((timer) => window.clearTimeout(timer));
      animTimersRef.current = [
        window.setTimeout(() => {
          setBornNodeIds(new Set());
          setBornEdgeIds(new Set());
        }, 700),
        window.setTimeout(() => {
          setSettledNodeIds(new Set());
          setShookNodeIds(new Set());
        }, 500),
        window.setTimeout(() => setCompletePingActive(false), 1500),
      ];
    }

    prevGraphRef.current = {
      nodeStatuses: nextStatuses,
      edgeIds: nextEdgeIds,
      conversationId: conversation?.id,
      status: logicalGraph.status,
    };
  }, [graph, conversation?.id, logicalGraph.status]);

  useEffect(
    () => () => {
      animTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    },
    [],
  );

  const fitGraph = useCallback(() => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    const viewportWidth = Math.max(320, bounds?.width || 900);
    const viewportHeight = Math.max(260, bounds?.height || 560);
    const padding = fullscreen ? 72 : 44;
    const scale = clampScale(
      Math.min(
        (viewportWidth - padding * 2) / graph.width,
        (viewportHeight - padding * 2) / graph.height,
      ),
    );
    setTransform({
      x: (viewportWidth - graph.width * scale) / 2,
      y: (viewportHeight - graph.height * scale) / 2,
      scale,
    });
    setUserMovedViewport(false);
  }, [fullscreen, graph.height, graph.width]);
  const fitGraphRef = useRef(fitGraph);
  fitGraphRef.current = fitGraph;

  useEffect(() => {
    setSelectedNodeId(undefined);
    setRevealedByRun({});
    setUserMovedViewport(false);
    const frame = window.requestAnimationFrame(() => fitGraphRef.current());
    return () => window.cancelAnimationFrame(frame);
  }, [conversation?.id]);

  useEffect(() => {
    if (selectedNodeId && !nodeById.has(selectedNodeId)) {
      setSelectedNodeId(undefined);
    }
  }, [nodeById, selectedNodeId]);

  useEffect(() => {
    if (userMovedViewport || typeof ResizeObserver === 'undefined') {
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const observer = new ResizeObserver(() => fitGraph());
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [fitGraph, userMovedViewport]);

  const zoomBy = useCallback((factor: number) => {
    setUserMovedViewport(true);
    setTransform((current) => ({ ...current, scale: clampScale(current.scale * factor) }));
  }, []);

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    if (isWorkflowOverlayTarget(event.target)) {
      return;
    }
    const bounds = canvasRef.current?.getBoundingClientRect();
    if (!bounds) {
      return;
    }
    const pointerX = event.clientX - bounds.left;
    const pointerY = event.clientY - bounds.top;
    const factor = event.deltaY < 0 ? 1.1 : 0.9;
    setUserMovedViewport(true);
    setTransform((current) => {
      const nextScale = clampScale(current.scale * factor);
      const ratio = nextScale / current.scale;
      return {
        x: pointerX - (pointerX - current.x) * ratio,
        y: pointerY - (pointerY - current.y) * ratio,
        scale: nextScale,
      };
    });
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (isWorkflowOverlayTarget(event.target)) {
      return;
    }
    if (event.target !== event.currentTarget && (event.target as Element).closest('[data-workflow-node]')) {
      return;
    }
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      originX: transform.x,
      originY: transform.y,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    setUserMovedViewport(true);
    setTransform((current) => ({
      ...current,
      x: drag.originX + event.clientX - drag.x,
      y: drag.originY + event.clientY - drag.y,
    }));
  }

  function handlePointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId === event.pointerId) {
      dragRef.current = null;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  }

  const revealToolGroup = useCallback((node: WorkflowGraphNode) => {
    if (node.kind !== 'tool-group' || !node.toolRunId || !node.hiddenToolCount) {
      return;
    }
    setUserMovedViewport(true);
    setRevealedByRun((current) => {
      const currentRevealCount = current[node.toolRunId] || 0;
      return {
        ...current,
        [node.toolRunId]: node.hiddenToolCount <= 3
          ? Number.POSITIVE_INFINITY
          : currentRevealCount + 3,
      };
    });
  }, []);

  const actionCount = Math.max(0, logicalGraph.nodes.length - 1);
  const isLive = logicalGraph.status === 'live';
  const showMinimap = graph.nodes.length > 30;

  /* P1-2 minimap 视口联动 */
  function handleMinimapNavigate(event: React.PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height || !graph.width || !graph.height) {
      return;
    }
    const ratioX = (event.clientX - bounds.left) / bounds.width;
    const ratioY = (event.clientY - bounds.top) / bounds.height;
    const canvasBounds = canvasRef.current?.getBoundingClientRect();
    const viewportWidth = canvasBounds?.width || 900;
    const viewportHeight = canvasBounds?.height || 560;
    setUserMovedViewport(true);
    setTransform((current) => ({
      ...current,
      x: viewportWidth / 2 - graph.width * ratioX * current.scale,
      y: viewportHeight / 2 - graph.height * ratioY * current.scale,
    }));
  }

  return (
    <aside
      role="complementary"
      aria-label={`Workflow for ${conversation?.title || 'Current Session'}`}
      className="aegis-workflow-panel h-full min-w-0 flex flex-col"
      data-testid="session-workflow"
    >
      {showHeader ? (
      <header className="h-16 shrink-0 px-4 border-b border-slate-800/90 bg-[#03080f]/95 flex items-center gap-3">
        <div className="aegis-status-glow--accent h-8 w-8 rounded-full border border-cyan-700/50 bg-cyan-950/30 text-cyan-300 flex items-center justify-center">
          <Workflow className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-bold tracking-[0.18em] text-white">SESSION WORKFLOW</div>
          <div className="mt-0.5 truncate text-[10px] font-mono text-slate-500">
            {conversation?.title || 'Current Session'}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            aria-label={fullscreen ? 'Exit workflow fullscreen' : 'Enter workflow fullscreen'}
            onClick={() => onFullscreenChange(!fullscreen)}
            className="h-8 w-8 rounded border border-slate-800 bg-[#07131d] text-slate-400 hover:text-amber-200 hover:border-amber-700/50 transition-colors flex items-center justify-center"
            title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          >
            {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          {onClose ? (
            <button
              type="button"
              aria-label="Close workflow visualization"
              onClick={onClose}
              className="h-8 w-8 rounded border border-slate-800 bg-[#07131d] text-slate-400 hover:text-cyan-300 hover:border-cyan-800/60 transition-colors flex items-center justify-center"
              title="Close workflow"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </header>
      ) : null}

      <div
        ref={canvasRef}
        className={`aegis-workflow-canvas relative flex-1 min-h-0 overflow-hidden select-none touch-none cursor-grab active:cursor-grabbing ${isLive ? 'workflow-canvas--live' : ''}`}
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setSelectedNodeId(undefined);
          }
        }}
        data-testid="workflow-canvas"
        data-scale={transform.scale.toFixed(2)}
        data-offset-x={transform.x.toFixed(2)}
        data-offset-y={transform.y.toFixed(2)}
      >
        <div className="aegis-workflow-atmosphere absolute inset-0 pointer-events-none opacity-80" />
        <div
          className="aegis-workflow-grid absolute inset-0 pointer-events-none opacity-25"
          style={{ maskImage: 'radial-gradient(ellipse at 42% 46%, black 30%, transparent 78%)', WebkitMaskImage: 'radial-gradient(ellipse at 42% 46%, black 30%, transparent 78%)' }}
        />

        <div data-workflow-overlay className="aegis-workflow-overlay absolute left-3 top-3 z-20 flex items-center gap-1 rounded border border-slate-800/90 p-1 backdrop-blur">
          <button type="button" aria-label="Zoom in workflow" onClick={() => zoomBy(1.15)} className="h-7 w-7 text-slate-400 hover:text-cyan-200 flex items-center justify-center">
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button type="button" aria-label="Zoom out workflow" onClick={() => zoomBy(0.87)} className="h-7 w-7 text-slate-400 hover:text-cyan-200 flex items-center justify-center">
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <button type="button" aria-label="Fit workflow to view" onClick={fitGraph} className="h-7 w-7 text-slate-400 hover:text-amber-200 flex items-center justify-center">
            <Focus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Reset workflow view"
            onClick={() => {
              setUserMovedViewport(true);
              setTransform({ x: 36, y: 36, scale: 1 });
            }}
            className="h-7 w-7 text-slate-400 hover:text-amber-200 flex items-center justify-center"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <span className="px-1.5 text-[9px] font-mono tabular-nums text-slate-500">
            {Math.round(transform.scale * 100)}%
          </span>
        </div>

        <svg
          className="absolute inset-0 h-full w-full overflow-visible"
          aria-label="Session execution tree"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setSelectedNodeId(undefined);
            }
          }}
        >
          <defs>
            {(Object.keys(NODE_GRADIENT_IDS) as Array<WorkflowGraphNode['kind']>).map((kind) => (
              <radialGradient key={kind} id={NODE_GRADIENT_IDS[kind]} cx="38%" cy="34%" r="72%">
                <stop offset="0%" stopColor={NODE_GRADIENT_CENTER[kind]} />
                <stop offset="100%" stopColor={NODE_COLORS[kind].fill} />
              </radialGradient>
            ))}
            <radialGradient id="workflow-grad-failed" cx="38%" cy="34%" r="72%">
              <stop offset="0%" stopColor="color-mix(in srgb, var(--aegis-danger-text) 62%, var(--aegis-text))" />
              <stop offset="100%" stopColor={FAILED_COLORS.fill} />
            </radialGradient>
          </defs>
          <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.scale})`}>
            {/* P2-1 主泳道跑道分隔线 */}
            {graph.nodes
              .filter((node) => node.kind === 'input' && node.source === 'main')
              .map((lane) => (
                <line
                  key={`lane-${lane.id}`}
                  x1="36"
                  x2={graph.width - 40}
                  y1={lane.y}
                  y2={lane.y}
                  stroke="color-mix(in srgb, var(--aegis-focus) 6%, transparent)"
                  strokeWidth="1"
                  strokeDasharray="2 10"
                  className="pointer-events-none"
                />
              ))}

            {/* P1-1 delegate 泳道领地 */}
            {delegateTerritories.map((territory) => (
              <g key={`territory-${territory.id}`} className="pointer-events-none">
                <rect
                  x={territory.x}
                  y={territory.y}
                  width={territory.width}
                  height={territory.height}
                  rx="16"
                  fill="color-mix(in srgb, var(--aegis-danger-text) 5%, transparent)"
                  stroke="color-mix(in srgb, var(--aegis-danger-text) 20%, transparent)"
                  strokeWidth="1"
                />
                <text
                  x={territory.x + 14}
                  y={territory.y + 16}
                  fill="var(--aegis-danger-text)"
                  fontSize="9"
                  fontFamily="JetBrains Mono, monospace"
                  letterSpacing="0.12em"
                  opacity="0.85"
                >
                  {territory.agent.toUpperCase()}
                </text>
              </g>
            ))}

            {/* P2-3 完成收束：root 扩散光环 */}
            {completePingActive && nodeById.get(graph.rootId) ? (
              <circle
                cx={nodeById.get(graph.rootId)?.x}
                cy={nodeById.get(graph.rootId)?.y}
                r="30"
                fill="none"
                stroke="var(--aegis-success-text)"
                strokeWidth="2.5"
                className="workflow-complete-ping pointer-events-none"
              />
            ) : null}

            {graph.edges.map((edge, edgeIndex) => {
              const from = nodeById.get(edge.from);
              const to = nodeById.get(edge.to);
              if (!from || !to) {
                return null;
              }
              const highlighted = !selectedNode || (highlightedNodeIds.has(edge.from) && highlightedNodeIds.has(edge.to));
              const surging = Boolean(selectedNode) && highlightedNodeIds.has(edge.from) && highlightedNodeIds.has(edge.to);
              const delegate = edge.source === 'delegate';
              /* P0-1 边分级：running 流动 / failed 红 / completed 沉淀 */
              const targetFailed = to.kind === 'end' && to.status !== 'completed';
              const targetRunning = to.status === 'running' || to.status === 'active';
              const flowClass = targetFailed
                ? 'workflow-edge-flow--failed'
                : surging
                  ? 'workflow-edge-flow--surge'
                  : targetRunning
                    ? 'workflow-edge-flow--live'
                    : 'workflow-edge-flow--done';
              const baseStroke = targetFailed ? 'var(--aegis-danger-text)' : delegate ? 'var(--aegis-ticker-actor)' : 'var(--aegis-ticker-object)';
              const flowStroke = targetFailed ? 'color-mix(in srgb, var(--aegis-danger-text) 62%, var(--aegis-text))' : delegate ? 'color-mix(in srgb, var(--aegis-ticker-actor) 62%, var(--aegis-text))' : 'var(--aegis-focus)';
              const draw = bornEdgeIds.has(edge.id);
              return (
                <g key={edge.id} opacity={highlighted ? 1 : 0.22} className="transition-opacity duration-200">
                  <path
                    data-testid={`workflow-edge-base-${edge.id}`}
                    data-edge-layer="base"
                    d={edgePath(from, to)}
                    fill="none"
                    stroke={baseStroke}
                    strokeWidth={highlighted ? 2 : 1.25}
                    strokeDasharray={delegate ? '7 6' : undefined}
                    vectorEffect="non-scaling-stroke"
                    className={targetRunning || targetFailed ? '' : 'workflow-edge-base--done'}
                  />
                  <path
                    data-testid={`workflow-edge-flow-${edge.id}`}
                    data-edge-layer="flow"
                    d={edgePath(from, to)}
                    fill="none"
                    stroke={flowStroke}
                    strokeWidth={highlighted ? 2.25 : 1.5}
                    strokeDasharray="3 18"
                    vectorEffect="non-scaling-stroke"
                    pathLength="100"
                    className={`workflow-edge-flow pointer-events-none ${flowClass} ${draw ? 'workflow-edge--draw' : ''}`}
                    style={{ animationDelay: draw ? undefined : `${-(edgeIndex % 9) * 0.12}s` }}
                  />
                  {edge.label ? (
                    <text
                      x={(from.x + to.x) / 2}
                      y={(from.y + to.y) / 2 - 9}
                      fill={delegate ? 'var(--aegis-danger-text)' : 'var(--aegis-ticker-object)'}
                      fontSize="8"
                      fontFamily="JetBrains Mono, monospace"
                      letterSpacing="0.08em"
                      textAnchor="middle"
                    >
                      {edge.label}
                    </text>
                  ) : null}
                </g>
              );
            })}

            {graph.nodes.map((node) => {
              const radius = nodeRadius(node, graph.nodes.length);
              const haloRadius = nodeHaloSize(node, radius, graph.nodes.length);
              const selected = node.id === selectedNodeId;
              const highlighted = !selectedNode || highlightedNodeIds.has(node.id);
              const failed = node.kind === 'end' && node.status !== 'completed';
              const toolGroup = node.kind === 'tool-group';
              const colors = failed ? FAILED_COLORS : NODE_COLORS[node.kind];
              const gradientId = failed ? 'workflow-grad-failed' : NODE_GRADIENT_IDS[node.kind];
              const isLivest = livestNode?.id === node.id;
              const nodeAnimClass = bornNodeIds.has(node.id)
                ? 'workflow-node--born'
                : settledNodeIds.has(node.id)
                  ? 'workflow-node--settled'
                  : shookNodeIds.has(node.id)
                    ? 'workflow-node--shook'
                    : '';
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-label={
                    toolGroup
                      ? `Show next ${Math.min(3, node.hiddenToolCount || 0)} of ${node.hiddenToolCount || 0} hidden tools`
                      : `Select ${node.detail || node.label}`
                  }
                  data-workflow-node
                  data-node-kind={node.kind}
                  data-testid={`workflow-node-${node.id}`}
                  data-highlighted={highlighted ? 'true' : 'false'}
                  transform={`translate(${node.x} ${node.y})`}
                  opacity={highlighted ? 1 : 0.24}
                  className={`workflow-node cursor-pointer outline-none transition-opacity duration-200 ${nodeAnimClass}`}
                  onMouseEnter={() => setHoveredNodeId(node.id)}
                  onMouseLeave={() => setHoveredNodeId((current) => (current === node.id ? undefined : current))}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (toolGroup) {
                      revealToolGroup(node);
                    } else {
                      setSelectedNodeId(node.id);
                    }
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      if (toolGroup) {
                        revealToolGroup(node);
                      } else {
                        setSelectedNodeId(node.id);
                      }
                    }
                  }}
                >
                  {/* P0-4 最新活动节点雷达 ping */}
                  {isLivest ? (
                    <circle
                      r={haloRadius + 6}
                      fill="none"
                      stroke={colors.stroke}
                      strokeWidth="1.5"
                      className="workflow-radar-ping pointer-events-none"
                    />
                  ) : null}
                  {/* P0-3 外层大气辉光（running 呼吸） */}
                  <circle
                    r={haloRadius + 9}
                    fill={colors.glow}
                    opacity="0.3"
                    className={`workflow-node-halo-outer pointer-events-none ${node.status === 'running' ? 'workflow-node-halo-outer--running' : ''}`}
                  />
                  <circle
                    data-testid={`workflow-node-halo-${node.kind}`}
                    r={haloRadius}
                    fill={colors.glow}
                    opacity={selected ? 0.88 : 0.58}
                    className="workflow-node-halo pointer-events-none transition-opacity duration-200"
                  />
                  <circle
                    data-testid={`workflow-node-focus-${node.kind}`}
                    r={haloRadius + 3}
                    fill="none"
                    stroke="var(--aegis-text)"
                    strokeWidth="1.5"
                    strokeDasharray="3 3"
                    className="workflow-node-focus-ring pointer-events-none"
                  />
                  {/* P0-3 root 旋转虚线环 */}
                  {node.kind === 'root' ? (
                    <circle
                      r={radius + 12}
                      fill="none"
                      stroke={colors.stroke}
                      strokeWidth="1"
                      strokeDasharray="4 9"
                      opacity="0.7"
                      className="workflow-root-ring pointer-events-none"
                    />
                  ) : null}
                  {node.kind === 'delegate' || toolGroup ? (
                    <circle
                      r={radius + 5}
                      fill="none"
                      stroke={colors.stroke}
                      strokeWidth="0.8"
                      opacity={selected ? 0.8 : 0.3}
                      className="pointer-events-none"
                    />
                  ) : null}
                  {node.status === 'running' ? (
                    <circle
                      r={radius + 7}
                      fill="none"
                      stroke={colors.stroke}
                      strokeWidth="1"
                      opacity="0.45"
                      className="motion-safe:animate-pulse"
                    />
                  ) : null}
                  {/* P0-3 渐变能量体 + 上半弧高光 */}
                  <circle
                    r={radius}
                    fill={`url(#${gradientId})`}
                    stroke={selected ? 'var(--aegis-text)' : colors.stroke}
                    strokeWidth={selected ? 2.5 : 1.25}
                    style={{ filter: selected ? `drop-shadow(0 0 10px ${colors.stroke})` : undefined }}
                  />
                  <path
                    d={`M ${-radius * 0.62} ${-radius * 0.42} A ${radius * 0.78} ${radius * 0.78} 0 0 1 ${radius * 0.62} ${-radius * 0.42}`}
                    fill="none"
                    stroke="color-mix(in srgb, var(--aegis-text) 40%, transparent)"
                    strokeWidth="1"
                    strokeLinecap="round"
                    className="pointer-events-none"
                  />
                  {/* P0-3 delegate 瞳孔 */}
                  {node.kind === 'delegate' ? (
                    <>
                      <circle r={radius * 0.34} fill="var(--aegis-bg)" opacity="0.9" />
                      <circle r={radius * 0.34} fill="none" stroke={colors.stroke} strokeWidth="0.9" opacity="0.75" />
                    </>
                  ) : null}
                  {/* P0-3 tool-group 层叠图标 */}
                  {toolGroup ? (
                    <g pointerEvents="none">
                      <rect x="-5.5" y="-4.5" width="10" height="8" rx="1.5" fill="none" stroke="var(--aegis-text)" strokeWidth="1.1" opacity="0.55" />
                      <rect x="-3.5" y="-2.5" width="10" height="8" rx="1.5" fill="var(--aegis-ticker-object)" stroke="var(--aegis-text)" strokeWidth="1.1" />
                    </g>
                  ) : null}
                  <text
                    x={radius + 9}
                    y="3.5"
                    fill={highlighted ? 'var(--aegis-text)' : 'var(--aegis-text-muted)'}
                    fontSize={graph.nodes.length > 100 ? 8 : graph.nodes.length > 45 ? 9 : 10}
                    fontWeight={node.kind === 'root' || node.kind === 'delegate' || toolGroup ? 700 : 500}
                    fontFamily="JetBrains Mono, monospace"
                    paintOrder="stroke"
                    stroke="var(--aegis-bg)"
                    strokeWidth="3"
                    strokeLinejoin="round"
                  >
                    {node.label}
                  </text>
                  {/* P2-2 hover 相对时间戳 */}
                  {node.timestamp ? (
                    <text
                      x={radius + 9}
                      y="15"
                      fill="var(--aegis-ticker-object)"
                      fontSize="8"
                      fontFamily="JetBrains Mono, monospace"
                      paintOrder="stroke"
                      stroke="var(--aegis-bg)"
                      strokeWidth="3"
                      strokeLinejoin="round"
                      className="workflow-node-time"
                      data-visible={hoveredNodeId === node.id ? 'true' : 'false'}
                    >
                      {formatRelativeTime(node.timestamp)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
        </svg>

        {logicalGraph.status === 'empty' ? (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="mt-24 text-center">
              <div className="text-[10px] font-mono tracking-[0.2em] text-cyan-500/70">AWAITING FIRST TURN</div>
              <div className="mt-2 text-[11px] text-slate-600">Execution branches will grow from Aegis in real time.</div>
            </div>
          </div>
        ) : null}

        {/* P1-2 minimap 全局导航 */}
        {showMinimap ? (
          <div
            data-workflow-overlay
            className={`aegis-workflow-overlay absolute bottom-4 z-20 rounded border border-slate-800/90 p-1 backdrop-blur ${selectedNode ? 'left-4' : 'right-4'}`}
            data-testid="workflow-minimap"
          >
            <svg
              width="160"
              height={Math.max(48, Math.min(120, (160 * graph.height) / Math.max(1, graph.width)))}
              viewBox={`0 0 ${graph.width} ${graph.height}`}
              className="cursor-crosshair"
              onPointerDown={(event) => {
                event.stopPropagation();
                handleMinimapNavigate(event);
              }}
              onWheel={(event) => event.stopPropagation()}
            >
              {graph.nodes.map((node) => (
                <circle
                  key={`mini-${node.id}`}
                  cx={node.x}
                  cy={node.y}
                  r={node.kind === 'root' ? 26 : 16}
                  fill={NODE_COLORS[node.kind].fill}
                  opacity={node.id === selectedNodeId ? 1 : 0.7}
                />
              ))}
              {(() => {
                const canvasBounds = canvasRef.current?.getBoundingClientRect();
                const vw = canvasBounds?.width || 900;
                const vh = canvasBounds?.height || 560;
                return (
                  <rect
                    x={-transform.x / transform.scale}
                    y={-transform.y / transform.scale}
                    width={vw / transform.scale}
                    height={vh / transform.scale}
                    fill="color-mix(in srgb, var(--aegis-focus) 6%, transparent)"
                    stroke="var(--aegis-focus)"
                    strokeWidth={14}
                    className="pointer-events-none"
                  />
                );
              })()}
            </svg>
          </div>
        ) : null}

        {selectedNode ? (
          <section
            data-workflow-overlay
            className="aegis-workflow-details workflow-details-panel absolute bottom-4 right-4 z-30 w-[min(22rem,calc(100%-2rem))] overflow-hidden rounded-lg border border-slate-700/80 backdrop-blur-md select-text touch-pan-y"
          >
            {/* P1-4 顶部渐变发光线，随节点 kind 变色 */}
            <div
              className="h-0.5 w-full"
              style={{
                background: `linear-gradient(90deg, transparent 0%, ${DETAIL_ACCENT[selectedNode.kind]} 38%, ${DETAIL_ACCENT[selectedNode.kind]} 62%, transparent 100%)`,
                boxShadow: `0 0 12px ${DETAIL_ACCENT[selectedNode.kind]}`,
              }}
            />
            <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
              <span className="text-[9px] font-mono font-bold tracking-[0.18em] text-cyan-300">NODE DETAILS</span>
              <button type="button" aria-label="Close node details" onClick={() => setSelectedNodeId(undefined)} className="text-slate-500 hover:text-white">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div
              data-testid="workflow-node-details-scroll"
              className="max-h-64 space-y-3 overflow-y-auto overscroll-contain p-3 text-[10px]"
            >
              <div>
                <div className="font-mono uppercase tracking-wider text-slate-600">Name</div>
                <div className="mt-1 text-sm font-semibold text-slate-100">{selectedNode.label}</div>
              </div>
              <div className="grid grid-cols-2 gap-3 font-mono">
                <div><span className="text-slate-600">TYPE</span><div className="mt-0.5 uppercase text-slate-300">{selectedNode.kind}</div></div>
                <div><span className="text-slate-600">STATUS</span><div className="mt-0.5 uppercase text-slate-300">{selectedNode.status}</div></div>
                <div><span className="text-slate-600">SOURCE</span><div className="mt-0.5 uppercase text-slate-300">{selectedNode.source}</div></div>
                <div><span className="text-slate-600">TIME</span><div className="mt-0.5 text-slate-300">{formatTimestamp(selectedNode.timestamp)}</div></div>
              </div>
              {selectedNode.turnId ? <div className="break-all font-mono text-slate-500">TURN · {selectedNode.turnId}</div> : null}
              {selectedNode.agent ? <div className="font-mono text-rose-300">AGENT · {selectedNode.agent}</div> : null}
              {selectedNode.detail ? <div className="whitespace-pre-wrap break-words rounded border border-slate-800 bg-[#02070c] p-2.5 leading-relaxed text-slate-200">{selectedNode.detail}</div> : null}
              {selectedNode.argsPreview ? <div><div className="mb-1 font-mono text-slate-600">ARGUMENTS</div><pre className="whitespace-pre-wrap break-words rounded-r border-l-2 border-cyan-500/70 bg-[#02070c] p-2 text-cyan-100/80">{selectedNode.argsPreview}</pre></div> : null}
              {selectedNode.resultPreview ? <div><div className="mb-1 font-mono text-slate-600">RESULT</div><pre className="whitespace-pre-wrap break-words rounded-r border-l-2 border-emerald-500/70 bg-[#02070c] p-2 text-emerald-100/80">{selectedNode.resultPreview}</pre></div> : null}
              {selectedNode.finalMessage ? (
                <div>
                  <div className="mb-1 font-mono font-semibold tracking-wider text-emerald-400/80">FINAL MESSAGE</div>
                  <div className="aegis-alert aegis-alert--success whitespace-pre-wrap break-words rounded p-2.5 leading-relaxed">
                    {selectedNode.finalMessage}
                  </div>
                </div>
              ) : null}
            </div>
          </section>
        ) : null}
      </div>

      <footer className="shrink-0 px-4 py-2.5 border-t border-slate-800 bg-[#03080f] flex items-center justify-between text-[9px] font-mono tracking-wider text-slate-500">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${logicalGraph.status === 'live' ? 'bg-amber-400 motion-safe:animate-pulse' : logicalGraph.status === 'complete' ? 'bg-emerald-400' : 'bg-slate-500'}`} />
          <span>{logicalGraph.status === 'partial' ? 'PARTIAL TRACE' : logicalGraph.status === 'live' ? 'LIVE TRACE' : logicalGraph.status === 'complete' ? 'STATIC TRACE' : 'SESSION TRACE'}</span>
        </div>
        <span>{actionCount} ACTIONS · {logicalGraph.nodes.filter((node) => node.kind === 'tool').length} TOOLS</span>
      </footer>
    </aside>
  );
}
