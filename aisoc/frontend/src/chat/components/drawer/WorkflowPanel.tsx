/**
 * 会话 workflow 图（移植自 aegis SessionWorkflow，按 MASTER v4 "Command Deck" 视觉：
 * 节点/边/状态色走统一 --aisoc-* 令牌随深/浅主题重着色，克制辉光；
 * 保留缩放平移、长工具链折叠展开与节点详情）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Focus, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';

import {
  compactWorkflowGraph,
  projectSessionWorkflow,
  WorkflowToolRunExpansion,
} from '../../lib/sessionWorkflow';
import { Conversation, WorkflowGraphNode } from '../../types';
import { WorkflowNodeDetails } from './WorkflowNodeDetails';

const MIN_SCALE = 0.35;
const MAX_SCALE = 2.5;

const NODE_FILL: Record<WorkflowGraphNode['kind'], string> = {
  root: 'var(--aisoc-warning)',
  input: 'var(--aisoc-accent)',
  delegate: 'var(--aisoc-danger)',
  tool: 'var(--aisoc-info)',
  'tool-group': 'var(--aisoc-muted)',
  end: 'var(--aisoc-success)',
};

interface ViewportTransform {
  x: number;
  y: number;
  scale: number;
}

function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

function nodeRadius(node: WorkflowGraphNode): number {
  if (node.kind === 'root') return 20;
  if (node.kind === 'delegate') return 14;
  if (node.kind === 'input') return 12;
  if (node.kind === 'tool-group') return 11;
  return 9;
}

function edgePath(from: WorkflowGraphNode, to: WorkflowGraphNode): string {
  const bend = Math.max(44, (to.x - from.x) * 0.48);
  return `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`;
}

export function WorkflowPanel({ conversation }: { conversation?: Conversation }) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; originX: number; originY: number } | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [transform, setTransform] = useState<ViewportTransform>({ x: 36, y: 36, scale: 1 });
  const [userMovedViewport, setUserMovedViewport] = useState(false);
  const [revealedByRun, setRevealedByRun] = useState<WorkflowToolRunExpansion>({});

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
        title: conversation?.title || 'Current session',
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
  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) : undefined;

  const fitGraph = useCallback(() => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    const viewportWidth = Math.max(280, bounds?.width || 420);
    const viewportHeight = Math.max(240, bounds?.height || 480);
    const padding = 36;
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
  }, [graph.height, graph.width]);
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
    if (userMovedViewport || typeof ResizeObserver === 'undefined') return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => fitGraphRef.current());
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [userMovedViewport]);

  const zoomBy = useCallback((factor: number) => {
    setUserMovedViewport(true);
    setTransform((current) => ({ ...current, scale: clampScale(current.scale * factor) }));
  }, []);

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    const bounds = canvasRef.current?.getBoundingClientRect();
    if (!bounds) return;
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
    // 缩放工具栏/节点详情等 UI 控件上的 pointerdown 不应触发画布拖拽——否则
    // Chromium 会把随后的 click 重定向到已 setPointerCapture 的画布本身，
    // 导致按钮点击“无效”。
    if ((event.target as Element).closest('button, [data-workflow-node]')) return;
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
    if (!drag || drag.pointerId !== event.pointerId) return;
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
    if (node.kind !== 'tool-group' || !node.toolRunId || !node.hiddenToolCount) return;
    setUserMovedViewport(true);
    setRevealedByRun((current) => ({
      ...current,
      [node.toolRunId as string]:
        (node.hiddenToolCount as number) <= 3
          ? Number.POSITIVE_INFINITY
          : (current[node.toolRunId as string] || 0) + 3,
    }));
  }, []);

  function activateNode(node: WorkflowGraphNode) {
    if (node.kind === 'tool-group') {
      revealToolGroup(node);
    } else {
      setSelectedNodeId(node.id);
    }
  }

  const actionCount = Math.max(0, logicalGraph.nodes.length - 1);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="session-workflow">
      <div
        ref={canvasRef}
        className="relative min-h-0 flex-1 cursor-grab touch-none select-none overflow-hidden bg-[var(--aisoc-bg)] active:cursor-grabbing"
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onClick={(event) => {
          if (event.target === event.currentTarget) setSelectedNodeId(undefined);
        }}
        data-testid="workflow-canvas"
      >
        {/* 缩放控制 */}
        <div className="absolute left-2 top-2 z-20 flex items-center gap-0.5 rounded-[var(--aisoc-radius-sm)] border border-[var(--aisoc-border)] bg-[var(--aisoc-panel-strong)] p-0.5">
          <button type="button" aria-label="Zoom in" onClick={() => zoomBy(1.15)} className="flex h-6 w-6 items-center justify-center text-[var(--aisoc-muted)] hover:text-[var(--aisoc-accent)]">
            <ZoomIn className="h-3 w-3" aria-hidden="true" />
          </button>
          <button type="button" aria-label="Zoom out" onClick={() => zoomBy(0.87)} className="flex h-6 w-6 items-center justify-center text-[var(--aisoc-muted)] hover:text-[var(--aisoc-accent)]">
            <ZoomOut className="h-3 w-3" aria-hidden="true" />
          </button>
          <button type="button" aria-label="Fit view" onClick={fitGraph} className="flex h-6 w-6 items-center justify-center text-[var(--aisoc-muted)] hover:text-[var(--aisoc-accent)]">
            <Focus className="h-3 w-3" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Reset view"
            onClick={() => {
              setUserMovedViewport(true);
              setTransform({ x: 36, y: 36, scale: 1 });
            }}
            className="flex h-6 w-6 items-center justify-center text-[var(--aisoc-muted)] hover:text-[var(--aisoc-accent)]"
          >
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
          </button>
          <span className="px-1 font-mono text-[9px] tabular-nums text-[var(--aisoc-muted)]">
            {Math.round(transform.scale * 100)}%
          </span>
        </div>

        <svg className="absolute inset-0 h-full w-full overflow-visible" aria-label="Session Execution Graph">
          <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.scale})`}>
            {graph.edges.map((edge) => {
              const from = nodeById.get(edge.from);
              const to = nodeById.get(edge.to);
              if (!from || !to) return null;
              const delegate = edge.source === 'delegate';
              const targetFailed = to.kind === 'end' && to.status !== 'completed';
              const stroke = targetFailed
                ? 'var(--aisoc-danger)'
                : delegate
                  ? 'color-mix(in srgb, var(--aisoc-danger) 55%, transparent)'
                  : 'color-mix(in srgb, var(--aisoc-border-strong) 85%, transparent)';
              return (
                <g key={edge.id}>
                  <path
                    d={edgePath(from, to)}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={1.5}
                    strokeDasharray={delegate ? '6 5' : undefined}
                    vectorEffect="non-scaling-stroke"
                  />
                  {edge.label ? (
                    <text
                      x={(from.x + to.x) / 2}
                      y={(from.y + to.y) / 2 - 8}
                      fill="var(--aisoc-muted)"
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
              const radius = nodeRadius(node);
              const selected = node.id === selectedNodeId;
              const failed = node.kind === 'end' && node.status !== 'completed';
              const fill = failed ? 'var(--aisoc-danger)' : NODE_FILL[node.kind];
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  data-workflow-node
                  data-node-kind={node.kind}
                  aria-label={
                    node.kind === 'tool-group'
                      ? `Expand ${node.hiddenToolCount || 0} collapsed tools`
                      : `View node ${node.label}`
                  }
                  transform={`translate(${node.x} ${node.y})`}
                  className="cursor-pointer outline-none"
                  onClick={(event) => {
                    event.stopPropagation();
                    activateNode(node);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      activateNode(node);
                    }
                  }}
                >
                  {node.status === 'running' || node.status === 'active' ? (
                    <circle
                      r={radius + 5}
                      fill="none"
                      stroke={fill}
                      strokeWidth="1"
                      opacity="0.5"
                      className="motion-safe:animate-pulse"
                    />
                  ) : null}
                  <circle
                    r={radius}
                    fill={fill}
                    fillOpacity={0.22}
                    stroke={selected ? 'var(--aisoc-text)' : fill}
                    strokeWidth={selected ? 2 : 1.25}
                  />
                  <circle r={Math.max(2.5, radius * 0.32)} fill={fill} />
                  <text
                    x={radius + 8}
                    y="3.5"
                    fill={selected ? 'var(--aisoc-text)' : 'var(--aisoc-muted)'}
                    fontSize="10"
                    fontWeight={node.kind === 'root' || node.kind === 'delegate' ? 700 : 500}
                    fontFamily="JetBrains Mono, monospace"
                    paintOrder="stroke"
                    stroke="var(--aisoc-bg)"
                    strokeWidth="3"
                    strokeLinejoin="round"
                  >
                    {node.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        {logicalGraph.status === 'empty' ? (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--aisoc-muted)]">
                Awaiting First Turn
              </div>
              <div className="mt-2 text-[11px] text-[var(--aisoc-muted)]">Execution branches grow in real time as the session progresses.</div>
            </div>
          </div>
        ) : null}

        {selectedNode ? (
          <WorkflowNodeDetails node={selectedNode} onClose={() => setSelectedNodeId(undefined)} />
        ) : null}
      </div>

      <footer className="flex shrink-0 items-center justify-between border-t border-[var(--aisoc-border)] bg-[var(--aisoc-bg-alt)] px-3 py-1.5 font-mono text-[9px] uppercase tracking-wider text-[var(--aisoc-muted)]">
        <div className="flex items-center gap-1.5">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              logicalGraph.status === 'live'
                ? 'bg-[var(--aisoc-warning)] motion-safe:animate-pulse'
                : logicalGraph.status === 'complete'
                  ? 'bg-[var(--aisoc-success)]'
                  : 'bg-[var(--aisoc-muted)]'
            }`}
            aria-hidden="true"
          />
          <span>
            {logicalGraph.status === 'partial'
              ? 'PARTIAL TRACE'
              : logicalGraph.status === 'live'
                ? 'LIVE TRACE'
                : logicalGraph.status === 'complete'
                  ? 'STATIC TRACE'
                  : 'SESSION TRACE'}
          </span>
        </div>
        <span>
          {actionCount} ACTIONS · {logicalGraph.nodes.filter((node) => node.kind === 'tool').length} TOOLS
        </span>
      </footer>
    </div>
  );
}
