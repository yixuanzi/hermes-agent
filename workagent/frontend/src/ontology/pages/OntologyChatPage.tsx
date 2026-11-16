import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { SendHorizontal, Bot, User, Sparkles, Wrench, RotateCw, AlertCircle, CheckCircle2, WifiOff, StopCircle, Check, X, ChevronRight, Terminal, Clipboard, ClipboardCheck } from 'lucide-react';

import { useOntologyOverview, useOntologyScan } from '../hooks/useOntology';
import { useOntologyChat } from '../hooks/useOntologyChat';
import { EmptyScan, isNoScan404 } from '../components/EmptyScan';
import { extractErrorMessage } from '../components/MutationStatus';
import { formatToolDuration, type ChatMessage } from '../../lib/useAgentChat';

const QUICK_PROMPTS = [
  '当前 completeness 分数为什么是这个值？主要缺口在哪个域？',
  '哪些能力应该最优先补齐？给出 Top 3 + 理由',
  'D0 AI 编排与知识底座目前满足程度如何？',
  '威胁情报（D5）域还缺什么？跑一次 scanner 看看',
];

export function OntologyChatPage() {
  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement | null>(null);
  const overviewQuery = useOntologyOverview();
  const scanMutation = useOntologyScan();
  const chat = useOntologyChat();

  const { phase, messages, activeApproval, activeClarify, error } = chat.state;
  const streaming = phase === 'streaming';
  const canSend = phase === 'idle' || phase === 'streaming';

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, streaming]);

  const submit = (q: string) => {
    if (!canSend) return;
    const text = q.trim();
    if (!text) return;
    setInput('');
    chat.send(text);
  };

  if (isNoScan404(overviewQuery.error)) {
    return (
      <div className="space-y-6">
        <div className="anim-fade-up">
          <div className="eyebrow mb-1">Onboarding · 本体对话</div>
          <h1 className="text-3xl font-bold font-display text-gradient">Ontology Chat</h1>
        </div>
        <EmptyScan
          title="本体对话依赖扫描证据"
          message="Ontology Chat 会调用 workagent-ontology skill 引用 mapped-graph 的证据来防幻觉；请先运行 Environment Scan 生成扫描快照。"
          onRunScan={() => scanMutation.mutate()}
          scanPending={scanMutation.isPending}
          scanError={scanMutation.isError ? extractErrorMessage(scanMutation.error, '扫描失败') : null}
        />
      </div>
    );
  }

  return (
    <div className="space-y-5 h-full flex flex-col">
      <div className="anim-fade-up">
        <div className="flex items-center gap-3 mb-1 flex-wrap">
          <div className="eyebrow">Onboarding · 本体对话</div>
          <PhaseBadge phase={phase} />
          <button
            className="btn-ghost text-xs"
            onClick={chat.startNewSession}
            disabled={streaming}
            title="结束当前会话并新建"
          >
            <RotateCw className="h-3 w-3" /> 新会话
          </button>
          {streaming ? (
            <button className="btn-ghost text-xs" onClick={chat.interrupt} title="中断当前回答">
              <StopCircle className="h-3 w-3" /> 中断
            </button>
          ) : null}
        </div>
        <h1 className="text-3xl font-bold font-display text-gradient">Ontology Chat</h1>
        <p className="text-sm text-[color:var(--ink-mid)] mt-1.5">
          回答 WORKAGENT 能力本体、扫描完整度、能力缺口相关问题；每条回答都会引用 mapped-graph 中的证据节点
          <span className="font-mono text-[color:var(--ink-lo)]"> (node_id) </span>
          以防幻觉，历史对话自动保存。
        </p>
      </div>

      {phase === 'disconnected' && error ? (
        <div className="glass p-5 anim-fade-up" style={{ borderColor: 'rgba(255,107,138,0.35)' }}>
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 mt-0.5" style={{ color: '#FF9DB2' }} />
            <div className="text-sm">
              <div className="text-[color:var(--ink-hi)] font-medium">LLM 会话不可用</div>
              <div className="text-[color:var(--ink-mid)] mt-1">{error}</div>
              <div className="text-[color:var(--ink-lo)] mt-2 text-xs">
                检查项：当前后端 profile 是否已注册且能加载 workagent-ontology skill（<code className="font-mono">hermes profiles list</code>）·
                后端启动时是否加了 <code className="font-mono">--tui</code> 或 <code className="font-mono">HERMES_WORKAGENT_TUI=1</code>
              </div>
              <button className="btn-primary mt-3 text-xs" onClick={chat.connect}>重新连接</button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="glass flex-1 flex flex-col min-h-[420px] overflow-hidden">
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {messages.length === 0 && phase === 'idle' && (
            <div className="h-full flex flex-col items-center justify-center text-center gap-4 py-10">
              <div className="h-14 w-14 rounded-2xl flex items-center justify-center glow-cyan anim-pulse" style={{ background: 'linear-gradient(150deg,#0C2436,#08131F)' }}>
                <Bot className="h-7 w-7" style={{ color: '#7FE9FF' }} />
              </div>
              <div>
                <div className="text-base font-medium text-[color:var(--ink-hi)]">与 WORKAGENT 本体助手对话</div>
                <div className="text-xs text-[color:var(--ink-lo)] mt-1 max-w-sm">
                  Agent 会调用 workagent-ontology skill 的 scanner / mapper 来实证回答，每条断言用 (node_id) 引用。
                </div>
              </div>
            </div>
          )}

          {messages.length === 0 && phase === 'connecting' && (
            <div className="h-full flex items-center justify-center text-xs text-[color:var(--ink-lo)] font-mono anim-pulse">
              建立会话中…
            </div>
          )}

          {groupMessages(messages).map((entry, i) =>
            Array.isArray(entry)
              ? <ToolGroupBubble key={`tools-${i}`} tools={entry} />
              : <MessageBubble key={entry.id} m={entry} />
          )}

          {streaming && !messages.some((m) => m.role === 'agent' && !m.done) ? (
            <div className="flex gap-3">
              <div className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'rgba(56,225,255,0.12)', border: '1px solid var(--stroke-soft)' }}>
                <Bot className="h-4 w-4" style={{ color: '#7FE9FF' }} />
              </div>
              <div className="rounded-2xl px-4 py-3 bg-white/[0.03] border border-[color:var(--stroke-soft)]">
                <span className="text-sm text-[color:var(--ink-lo)] font-mono anim-pulse">agent 思考中…</span>
              </div>
            </div>
          ) : null}

          {activeApproval ? (
            <ApprovalCard
              toolName={activeApproval.tool_name || 'tool'}
              command={activeApproval.command}
              onAccept={() => chat.respondApproval(true)}
              onReject={() => chat.respondApproval(false)}
            />
          ) : null}

          {activeClarify ? (
            <ClarifyCard
              question={activeClarify.question}
              choices={activeClarify.choices}
              onChoose={(c) => chat.respondClarify(c)}
            />
          ) : null}

          <div ref={endRef} />
        </div>

        {messages.length === 0 && phase === 'idle' && (
          <div className="px-5 pb-3 flex flex-wrap gap-2">
            {QUICK_PROMPTS.map((p) => (
              <button key={p} className="btn-ghost text-xs" onClick={() => submit(p)} disabled={!canSend}>
                <Sparkles className="h-3 w-3" style={{ color: '#7FE9FF' }} />{p}
              </button>
            ))}
          </div>
        )}

        <div className="border-t p-3 flex items-center gap-2" style={{ borderColor: 'var(--stroke-soft)' }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(input); }}
            className="flex-1 rounded-xl bg-white/[0.03] border px-4 py-3 text-sm text-[color:var(--ink-hi)] placeholder:text-[color:var(--ink-lo)] focus:outline-none focus:border-[rgba(56,225,255,0.4)] disabled:opacity-50"
            style={{ borderColor: 'var(--stroke-soft)' }}
            placeholder={canSend ? '问点什么，agent 会用 workagent-ontology skill 实证回答…' : '会话建立中…'}
            disabled={!canSend}
          />
          <button className="btn-primary" disabled={!canSend || !input.trim()} onClick={() => submit(input)}>
            <SendHorizontal className="h-4 w-4" /> 发送
          </button>
        </div>
      </div>
    </div>
  );
}

type ToolMsg = Extract<ChatMessage, { role: 'tool' }>;

/**
 * Group consecutive tool messages into runs. Also swap
 * [agent, tool-group] → [tool-group, agent] so tools render *above* the answer
 * they produced — same UX pattern FloatingChat uses.
 */
function groupMessages(messages: ChatMessage[]): (ChatMessage | ToolMsg[])[] {
  const raw: (ChatMessage | ToolMsg[])[] = [];
  let toolBuf: ToolMsg[] = [];
  for (const m of messages) {
    if (m.role === 'tool') {
      toolBuf.push(m);
    } else {
      if (toolBuf.length) { raw.push(toolBuf); toolBuf = []; }
      raw.push(m);
    }
  }
  if (toolBuf.length) raw.push(toolBuf);

  const result: (ChatMessage | ToolMsg[])[] = [];
  for (let i = 0; i < raw.length; i++) {
    const cur = raw[i];
    const next = raw[i + 1];
    if (!Array.isArray(cur) && cur.role === 'agent' && Array.isArray(next)) {
      result.push(next);
      result.push(cur);
      i++;
    } else {
      result.push(cur);
    }
  }
  return result;
}

function ToolGroupBubble({ tools }: { tools: ToolMsg[] }) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const toggle = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const runningCount = tools.filter((t) => t.status === 'running').length;
  const doneCount = tools.length - runningCount;
  const anyRunning = runningCount > 0;

  return (
    <div className="flex gap-3 anim-fade-up">
      <div
        className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0"
        style={{
          background: anyRunning ? 'rgba(255,194,75,0.14)' : 'rgba(52,229,163,0.10)',
          border: `1px solid ${anyRunning ? 'rgba(255,194,75,0.40)' : 'rgba(52,229,163,0.35)'}`,
          transition: 'background 200ms ease, border-color 200ms ease',
        }}
      >
        <Wrench
          className={`h-4 w-4 ${anyRunning ? 'anim-pulse' : ''}`}
          style={{ color: anyRunning ? '#FFD98A' : '#7CF3C8' }}
        />
      </div>
      <div
        className="flex-1 min-w-0 rounded-2xl border overflow-hidden"
        style={{
          borderColor: anyRunning ? 'rgba(255,194,75,0.30)' : 'var(--stroke-soft)',
          background: 'linear-gradient(155deg, rgba(20,30,50,0.55), rgba(10,16,28,0.45))',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-2 px-3 py-2 border-b"
          style={{
            borderColor: 'var(--stroke-soft)',
            background: anyRunning ? 'rgba(255,194,75,0.05)' : 'transparent',
          }}
        >
          <Terminal className="h-3.5 w-3.5 shrink-0" style={{ color: anyRunning ? '#FFD98A' : '#7CF3C8' }} />
          <span className="eyebrow" style={{ letterSpacing: '0.14em' }}>
            Skill 调用
          </span>
          <span className="text-[11px] text-[color:var(--ink-lo)] font-mono">
            {anyRunning
              ? `${runningCount}/${tools.length} 进行中`
              : `${tools.length} 项 · 已完成`}
          </span>
          {anyRunning ? (
            <span className="ml-auto flex items-center gap-1 text-[10px] font-mono text-[color:var(--st-partial)]">
              <span className="h-1.5 w-1.5 rounded-full anim-pulse" style={{ background: '#FFD98A', boxShadow: '0 0 6px #FFD98A' }} />
              running
            </span>
          ) : (
            <span className="ml-auto flex items-center gap-1 text-[10px] font-mono" style={{ color: '#7CF3C8' }}>
              <CheckCircle2 className="h-3 w-3" />
              done · {doneCount} 项
            </span>
          )}
        </div>

        {/* Tool rows */}
        <div className="divide-y" style={{ borderColor: 'var(--stroke-soft)' }}>
          {tools.map((tool) => {
            const expanded = expandedIds.has(tool.id);
            const isDone = tool.status === 'done';
            const hasDetail = !!(tool.context || tool.summary);
            return (
              <div key={tool.id} style={{ borderTop: '1px solid var(--stroke-soft)' }}>
                <button
                  type="button"
                  onClick={() => hasDetail && toggle(tool.id)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left"
                  style={{
                    background: expanded ? 'rgba(255,255,255,0.02)' : 'transparent',
                    cursor: hasDetail ? 'pointer' : 'default',
                    transition: 'background 120ms ease',
                    border: 'none',
                    color: 'inherit',
                    font: 'inherit',
                  }}
                  aria-expanded={hasDetail ? expanded : undefined}
                  disabled={!hasDetail}
                  onMouseEnter={(e) => { if (hasDetail && !expanded) e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; }}
                  onMouseLeave={(e) => { if (hasDetail && !expanded) e.currentTarget.style.background = 'transparent'; }}
                >
                  {hasDetail ? (
                    <ChevronRight
                      className="h-3.5 w-3.5 shrink-0"
                      style={{
                        color: 'var(--ink-lo)',
                        transition: 'transform 160ms ease',
                        transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                      }}
                    />
                  ) : (
                    <span className="h-3.5 w-3.5 shrink-0" />
                  )}
                  {isDone ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" style={{ color: '#7CF3C8' }} />
                  ) : (
                    <span
                      className="h-3.5 w-3.5 shrink-0 rounded-full anim-pulse"
                      style={{ background: 'rgba(255,194,75,0.15)', border: '1.5px solid #FFD98A', boxShadow: '0 0 8px rgba(255,194,75,0.5)' }}
                      aria-hidden
                    />
                  )}
                  <span
                    className="font-mono text-[12px] truncate flex-1 min-w-0"
                    style={{ color: 'var(--ink-hi)' }}
                    title={tool.name}
                  >
                    {tool.name}
                  </span>
                  {isDone && tool.duration_s != null ? (
                    <span className="text-[10px] font-mono text-[color:var(--ink-lo)] shrink-0">
                      {formatToolDuration(tool.duration_s)}
                    </span>
                  ) : (
                    <span className="text-[10px] font-mono text-[color:var(--st-partial)] shrink-0">
                      …
                    </span>
                  )}
                </button>
                {expanded && hasDetail ? (
                  <div className="px-3 pb-3 pt-1 space-y-2 anim-fade-up">
                    {tool.context ? (
                      <ToolDetailSection label="Args" content={tool.context} accent="#4DA3FF" />
                    ) : null}
                    {tool.summary ? (
                      <ToolDetailSection label="Result" content={tool.summary} accent="#34E5A3" />
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ToolDetailSection({ label, content, accent }: { label: string; content: string; accent: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard blocked — noop */
    }
  };
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[9px] uppercase font-semibold font-mono" style={{ color: accent, letterSpacing: '0.14em' }}>
          {label}
        </span>
        <button
          type="button"
          onClick={onCopy}
          className="ml-auto flex items-center gap-1 text-[10px] font-mono text-[color:var(--ink-lo)] hover:text-[color:var(--ink-hi)]"
          style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 4px' }}
          title={copied ? '已复制' : '复制'}
        >
          {copied ? <ClipboardCheck className="h-3 w-3" /> : <Clipboard className="h-3 w-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre
        className="text-[11px] font-mono rounded-md p-2 m-0 overflow-x-auto"
        style={{
          color: 'var(--ink-mid)',
          background: 'rgba(0,0,0,0.28)',
          border: '1px solid var(--stroke-soft)',
          maxHeight: 220,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {content}
      </pre>
    </div>
  );
}

function MessageBubble({ m }: { m: ChatMessage }) {
  if (m.role === 'tool') {
    // Tool messages are rendered inside ToolGroupBubble; only user/agent reach here.
    return null;
  }

  const isUser = m.role === 'user';
  return (
    <div className={`flex gap-3 anim-fade-up ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: isUser ? 'rgba(77,163,255,0.15)' : 'rgba(56,225,255,0.12)', border: '1px solid var(--stroke-soft)' }}>
        {isUser ? <User className="h-4 w-4" style={{ color: '#9CC4FF' }} /> : <Bot className="h-4 w-4" style={{ color: '#7FE9FF' }} />}
      </div>
      <div className={`max-w-[80%] rounded-2xl px-4 py-3 ${isUser ? 'bg-[rgba(77,163,255,0.10)] border border-[rgba(77,163,255,0.25)]' : 'bg-white/[0.03] border border-[color:var(--stroke-soft)]'}`}>
        <div className="text-sm text-[color:var(--ink-hi)] leading-relaxed ontology-md">
          {isUser ? (
            <div className="whitespace-pre-wrap">{m.text}</div>
          ) : (
            <ReactMarkdown>{m.text}</ReactMarkdown>
          )}
          {!isUser && !m.done ? (
            <span className="inline-block ml-1 anim-pulse text-[color:var(--ink-lo)]">▊</span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ApprovalCard({
  toolName, command, onAccept, onReject,
}: { toolName: string; command?: string; onAccept: () => void; onReject: () => void }) {
  return (
    <div className="glass p-4 anim-fade-up" style={{ borderColor: 'rgba(255,194,75,0.35)' }}>
      <div className="flex items-start gap-3">
        <AlertCircle className="h-5 w-5 mt-0.5" style={{ color: '#FFD98A' }} />
        <div className="flex-1">
          <div className="text-sm text-[color:var(--ink-hi)] font-medium">Agent 请求执行工具</div>
          <div className="text-xs text-[color:var(--ink-mid)] mt-1">
            <span className="font-mono">{toolName}</span>
          </div>
          {command ? (
            <pre className="text-[11px] text-[color:var(--ink-lo)] font-mono mt-2 p-2 rounded border overflow-x-auto" style={{ borderColor: 'var(--stroke-soft)', background: 'rgba(0,0,0,0.3)' }}>{command}</pre>
          ) : null}
          <div className="flex gap-2 mt-3">
            <button className="btn-primary text-xs" onClick={onAccept}><Check className="h-3 w-3" /> 允许</button>
            <button className="btn-ghost text-xs" onClick={onReject}><X className="h-3 w-3" /> 拒绝</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ClarifyCard({
  question, choices, onChoose,
}: { question: string; choices: string[]; onChoose: (choice: string) => void }) {
  return (
    <div className="glass p-4 anim-fade-up" style={{ borderColor: 'rgba(77,163,255,0.35)' }}>
      <div className="text-sm text-[color:var(--ink-hi)]">{question}</div>
      <div className="flex flex-wrap gap-2 mt-3">
        {choices.map((c) => (
          <button key={c} className="btn-ghost text-xs" onClick={() => onChoose(c)}>{c}</button>
        ))}
      </div>
    </div>
  );
}

function PhaseBadge({ phase }: { phase: string }) {
  if (phase === 'idle' || phase === 'streaming') {
    return (
      <span className="chip chip-satisfied" style={{ padding: '1px 8px', fontSize: 10 }}>
        <CheckCircle2 className="h-3 w-3" /> session · {phase === 'streaming' ? 'streaming' : 'ready'}
      </span>
    );
  }
  if (phase === 'connecting') {
    return (
      <span className="chip chip-partial" style={{ padding: '1px 8px', fontSize: 10 }}>
        <Sparkles className="h-3 w-3" /> session · connecting
      </span>
    );
  }
  return (
    <span className="chip chip-missing" style={{ padding: '1px 8px', fontSize: 10 }}>
      <WifiOff className="h-3 w-3" /> session · offline
    </span>
  );
}
