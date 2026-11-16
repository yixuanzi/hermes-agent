import { describe, expect, it } from 'vitest';
import {
  applyWorkflowSocketEvent,
  compactWorkflowGraph,
  projectSessionWorkflow,
} from './sessionWorkflow';
import { WorkflowGraph } from '../types';

function linearToolGraph(toolCount: number): WorkflowGraph {
  const rootId = 'session:test';
  const nodes: WorkflowGraph['nodes'] = [
    {
      id: rootId,
      kind: 'root',
      label: 'AEGIS',
      detail: 'Test session',
      status: 'active',
      source: 'main',
      x: 72,
      y: 120,
      depth: 0,
    },
    {
      id: 'turn:test',
      kind: 'input',
      label: 'Test turn',
      detail: 'Test turn',
      status: 'completed',
      source: 'main',
      turnId: 'test',
      parentId: rootId,
      x: 268,
      y: 120,
      depth: 1,
    },
  ];
  for (let index = 1; index <= toolCount; index += 1) {
    nodes.push({
      id: `tool:test:${index}`,
      kind: 'tool',
      label: `tool-${index}`,
      detail: `tool-${index}`,
      status: 'completed',
      source: 'main',
      turnId: 'test',
      parentId: index === 1 ? 'turn:test' : `tool:test:${index - 1}`,
      x: 268 + index * 196,
      y: 120,
      depth: index + 1,
    });
  }
  nodes.push({
    id: 'end:main:test',
    kind: 'end',
    label: 'Task complete',
    detail: 'idle',
    status: 'completed',
    source: 'main',
    turnId: 'test',
    parentId: toolCount ? `tool:test:${toolCount}` : 'turn:test',
    x: 268 + toolCount * 196 + 172,
    y: 120,
    depth: toolCount + 2,
  });
  return {
    rootId,
    nodes,
    edges: nodes.flatMap((node) => node.parentId ? [{
      id: `${node.parentId}->${node.id}`,
      from: node.parentId,
      to: node.id,
      source: node.source,
    }] : []),
    status: 'complete',
    width: 268 + toolCount * 196 + 392,
    height: 360,
  };
}

describe('compactWorkflowGraph', () => {
  it('collapses a four-tool run at the inclusive threshold without mutating the original graph', () => {
    const graph = linearToolGraph(4);
    const before = structuredClone(graph);
    const compact = compactWorkflowGraph(graph);

    expect(compact.nodes.filter((node) => node.kind === 'tool').map((node) => node.id)).toEqual([
      'tool:test:1',
      'tool:test:4',
    ]);
    expect(compact.nodes.find((node) => node.kind === 'tool-group')).toMatchObject({
      label: '+2 tools',
      hiddenToolCount: 2,
      source: 'main',
    });
    expect(graph).toEqual(before);
  });

  it('replaces the middle of a long tool run and compacts its layout', () => {
    const graph = linearToolGraph(8);
    const compact = compactWorkflowGraph(graph);
    const group = compact.nodes.find((node) => node.kind === 'tool-group');

    expect(compact.nodes.filter((node) => node.kind === 'tool').map((node) => node.id)).toEqual([
      'tool:test:1',
      'tool:test:8',
    ]);
    expect(group).toMatchObject({
      id: 'tool-group:tool:test:1:tool:test:8',
      label: '+6 tools',
      hiddenToolCount: 6,
      toolRunId: 'tool-run:tool:test:1',
      parentId: 'tool:test:1',
    });
    expect(compact.nodes.find((node) => node.id === 'tool:test:8')).toMatchObject({
      parentId: group?.id,
      depth: 4,
    });
    expect(compact.width).toBeLessThan(graph.width);
  });

  it('reveals three hidden tools at a time from the start of the run', () => {
    const graph = linearToolGraph(10);
    const firstExpansion = compactWorkflowGraph(graph, { 'tool-run:tool:test:1': 3 });

    expect(firstExpansion.nodes.filter((node) => node.kind === 'tool').map((node) => node.id)).toEqual([
      'tool:test:1',
      'tool:test:2',
      'tool:test:3',
      'tool:test:4',
      'tool:test:10',
    ]);
    expect(firstExpansion.nodes.find((node) => node.kind === 'tool-group')).toMatchObject({
      label: '+5 tools',
      hiddenToolCount: 5,
      parentId: 'tool:test:4',
    });

    const secondExpansion = compactWorkflowGraph(graph, { 'tool-run:tool:test:1': 6 });
    expect(secondExpansion.nodes.find((node) => node.kind === 'tool-group')).toMatchObject({
      label: '+2 tools',
      hiddenToolCount: 2,
      parentId: 'tool:test:7',
    });

    const fullyExpanded = compactWorkflowGraph(graph, {
      'tool-run:tool:test:1': Number.POSITIVE_INFINITY,
    });
    expect(fullyExpanded.nodes.some((node) => node.kind === 'tool-group')).toBe(false);
    expect(fullyExpanded.nodes.filter((node) => node.kind === 'tool')).toHaveLength(10);
  });

  it('splits at a same-source branch and folds only the downstream four-tool run', () => {
    const graph = linearToolGraph(7);
    const branchParent = graph.nodes.find((node) => node.id === 'tool:test:3');
    const end = graph.nodes.find((node) => node.id === 'end:main:test');
    if (!branchParent || !end) {
      throw new Error('Expected test graph nodes');
    }
    end.parentId = branchParent.id;
    graph.edges = graph.nodes.flatMap((node) => node.parentId ? [{
      id: `${node.parentId}->${node.id}`,
      from: node.parentId,
      to: node.id,
      source: node.source,
    }] : []);

    const compact = compactWorkflowGraph(graph);
    const group = compact.nodes.find((node) => node.kind === 'tool-group');
    expect(group).toMatchObject({
      id: 'tool-group:tool:test:4:tool:test:7',
      label: '+2 tools',
      parentId: 'tool:test:4',
    });
    expect(compact.nodes.find((node) => node.id === 'tool:test:3')).toMatchObject({
      parentId: 'tool:test:2',
    });
    expect(compact.nodes.find((node) => node.id === 'end:main:test')).toMatchObject({
      parentId: 'tool:test:3',
    });
  });

  it('folds a main tool backbone even when a hidden tool owns a delegate side branch', () => {
    const graph = linearToolGraph(7);
    graph.nodes.push({
      id: 'delegate:test:side-branch',
      kind: 'delegate',
      label: 'Side delegate',
      detail: 'Side delegate',
      status: 'running',
      source: 'delegate',
      turnId: 'test',
      parentId: 'tool:test:3',
      agent: 'side-agent',
      x: 1052,
      y: 200,
      depth: 5,
    });
    graph.edges.push({
      id: 'tool:test:3->delegate:test:side-branch',
      from: 'tool:test:3',
      to: 'delegate:test:side-branch',
      source: 'delegate',
      label: 'DELEGATE · side-agent',
    });

    const compact = compactWorkflowGraph(graph);
    const group = compact.nodes.find((node) => node.kind === 'tool-group');

    expect(group).toMatchObject({
      label: '+5 tools',
      source: 'main',
      parentId: 'tool:test:1',
    });
    expect(compact.nodes.find((node) => node.id === 'tool:test:7')).toMatchObject({
      parentId: group?.id,
    });
    expect(compact.nodes.find((node) => node.id === 'delegate:test:side-branch')).toMatchObject({
      parentId: group?.id,
    });
    expect(compact.edges.find((edge) => edge.to === 'delegate:test:side-branch')).toMatchObject({
      source: 'delegate',
      label: 'DELEGATE · side-agent',
    });
  });
});

describe('projectSessionWorkflow', () => {
  it('branches every main user turn directly from the Aegis session root', () => {
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-1',
          sender: 'user',
          kind: 'chat',
          text: 'Investigate the phishing alert',
          timestamp: '10:00',
          turnId: 'turn-1',
          source: 'main',
        },
        {
          id: 'user-2',
          sender: 'user',
          kind: 'chat',
          text: 'Check the sender infrastructure',
          timestamp: '10:03',
          turnId: 'turn-2',
          source: 'main',
        },
      ],
      trace: [
        {
          id: 'event-1',
          type: 'message.accepted',
          timestamp: 1,
          turnId: 'turn-1',
          source: 'main',
          clientMsgId: 'client-1',
        },
        {
          id: 'event-2',
          type: 'message.accepted',
          timestamp: 2,
          turnId: 'turn-2',
          source: 'main',
          clientMsgId: 'client-2',
        },
      ],
    });

    expect(graph.rootId).toBe('session:conversation-1');
    expect(graph.nodes.filter((node) => node.kind === 'input').map((node) => node.id)).toEqual([
      'turn:turn-1',
      'turn:turn-2',
    ]);
    expect(graph.edges.filter((edge) => edge.from === graph.rootId).map((edge) => edge.to)).toEqual([
      'turn:turn-1',
      'turn:turn-2',
    ]);
  });

  it('branches delegate user turns beneath their delegate node', () => {
    const delegateId = 'delegate:main-turn:delegate-call';
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          kind: 'chat',
          text: 'Investigate the alert',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
        {
          id: 'user-delegate-1',
          sender: 'user',
          kind: 'chat',
          text: 'Inspect the headers',
          timestamp: '10:01',
          turnId: 'delegate-turn-1',
          source: 'delegate',
          workflowParentId: delegateId,
        },
        {
          id: 'user-delegate-2',
          sender: 'user',
          kind: 'chat',
          text: 'Now pivot on the domain',
          timestamp: '10:02',
          turnId: 'delegate-turn-2',
          source: 'delegate',
          workflowParentId: delegateId,
        },
      ],
      trace: [
        {
          id: 'accepted-main',
          type: 'message.accepted',
          timestamp: 1,
          turnId: 'main-turn',
          source: 'main',
        },
        {
          id: 'delegate-started',
          type: 'tool.started',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'delegate-call',
          argsPreview: '{"agent_name":"threat-intel"}',
          delegateId,
          srcagent: 'threat-intel',
        },
        {
          id: 'accepted-delegate-1',
          type: 'message.accepted',
          timestamp: 3,
          turnId: 'delegate-turn-1',
          parentTurnId: 'main-turn',
          source: 'delegate',
          delegateId,
          srcagent: 'threat-intel',
        },
        {
          id: 'accepted-delegate-2',
          type: 'message.accepted',
          timestamp: 4,
          turnId: 'delegate-turn-2',
          parentTurnId: 'main-turn',
          source: 'delegate',
          delegateId,
          srcagent: 'threat-intel',
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === delegateId)).toMatchObject({
      kind: 'delegate',
      parentId: 'turn:main-turn',
      agent: 'threat-intel',
    });
    expect(graph.edges.filter((edge) => edge.from === delegateId).map((edge) => edge.to)).toEqual([
      'turn:delegate-turn-1',
      'turn:delegate-turn-2',
    ]);
  });

  it('shows autonomous delegate tools beneath the delegate even before user input', () => {
    const delegateId = 'delegate:main-turn:delegate-call';
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          text: 'Delegate the alert investigation',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
      ],
      trace: [
        { id: 'accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'delegate',
          type: 'tool.started',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'delegate-call',
          delegateId,
          srcagent: 'threat-intel',
        },
        {
          id: 'delegate-tool',
          type: 'tool.started',
          timestamp: 3,
          turnId: 'main-turn',
          source: 'delegate',
          toolName: 'terminal',
          toolCallId: 'delegate-tool-call',
          delegateId,
          srcagent: 'threat-intel',
        },
        {
          id: 'delegate-waiting',
          type: 'run.state',
          timestamp: 4,
          turnId: 'main-turn',
          source: 'delegate',
          state: 'waiting_for_delegate_input',
          delegateId,
          srcagent: 'threat-intel',
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === 'tool:main-turn:delegate-tool-call')).toMatchObject({
      source: 'delegate',
      parentId: delegateId,
      agent: 'threat-intel',
    });
    expect(graph.nodes.find((node) => node.id === `end:delegate:${delegateId}:main-turn`)).toMatchObject({
      source: 'delegate',
      parentId: 'tool:main-turn:delegate-tool-call',
    });
  });

  it('updates a started tool in place when its completion arrives', () => {
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          kind: 'chat',
          text: 'Inspect the host',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
      ],
      trace: [
        {
          id: 'accepted-main',
          type: 'message.accepted',
          timestamp: 1,
          turnId: 'main-turn',
          source: 'main',
        },
        {
          id: 'tool-started',
          type: 'tool.started',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'terminal',
          toolCallId: 'tool-1',
          argsPreview: '{"cmd":"pwd"}',
        },
        {
          id: 'tool-completed',
          type: 'tool.completed',
          timestamp: 3,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'terminal',
          toolCallId: 'tool-1',
          resultPreview: '/Users/demo',
        },
      ],
    });

    expect(graph.nodes.filter((node) => node.id === 'tool:main-turn:tool-1')).toHaveLength(1);
    expect(graph.nodes.find((node) => node.id === 'tool:main-turn:tool-1')).toMatchObject({
      kind: 'tool',
      status: 'completed',
      argsPreview: '{"cmd":"pwd"}',
      resultPreview: '/Users/demo',
      parentId: 'turn:main-turn',
    });
  });

  it('ends a main turn after its final action when the run becomes idle', () => {
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          kind: 'chat',
          text: 'Inspect the host',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
      ],
      trace: [
        {
          id: 'accepted-main',
          type: 'message.accepted',
          timestamp: 1,
          turnId: 'main-turn',
          source: 'main',
        },
        {
          id: 'tool-completed',
          type: 'tool.completed',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'terminal',
          toolCallId: 'tool-1',
          resultPreview: 'done',
        },
        {
          id: 'run-idle',
          type: 'run.state',
          timestamp: 3,
          turnId: 'main-turn',
          source: 'main',
          state: 'idle',
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === 'end:main:main-turn')).toMatchObject({
      kind: 'end',
      status: 'completed',
      parentId: 'tool:main-turn:tool-1',
    });
    expect(graph.status).toBe('complete');
  });

  it('includes the final main reply in the stable main end node', () => {
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          text: 'Summarize the investigation',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
        {
          id: 'assistant-main',
          sender: 'aegis',
          text: 'The alert is a confirmed credential phishing campaign.',
          timestamp: '10:01',
          turnId: 'main-turn',
          source: 'main',
        },
      ],
      trace: [
        { id: 'accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'reply',
          type: 'message.completed',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          messageId: 'assistant-main',
          content: 'The alert is a confirmed credential phishing campaign.',
        },
        { id: 'idle', type: 'run.state', timestamp: 3, turnId: 'main-turn', source: 'main', state: 'idle' },
      ],
    });

    expect(graph.nodes.find((node) => node.id === 'end:main:main-turn')).toMatchObject({
      kind: 'end',
      status: 'completed',
      finalMessage: 'The alert is a confirmed credential phishing campaign.',
    });
  });

  it('renders a delegate final without tools and resolves streamed content by message id', () => {
    const delegateId = 'delegate:main-turn:delegate-call';
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          text: 'Delegate the analysis',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
        {
          id: 'delegate-stream',
          sender: 'aegis',
          text: 'The delegated analysis found three malicious domains.',
          timestamp: '10:01',
          turnId: 'main-turn',
          source: 'delegate',
          srcagent: 'threat-intel',
        },
      ],
      trace: [
        { id: 'accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'delegate',
          type: 'tool.started',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'delegate-call',
          delegateId,
          srcagent: 'threat-intel',
        },
        {
          id: 'delegate-final',
          type: 'message.stream.completed',
          timestamp: 3,
          turnId: 'main-turn',
          source: 'delegate',
          delegateId,
          srcagent: 'threat-intel',
          messageId: 'delegate-stream',
        },
        {
          id: 'delegate-waiting',
          type: 'run.state',
          timestamp: 4,
          turnId: 'main-turn',
          source: 'delegate',
          delegateId,
          srcagent: 'threat-intel',
          state: 'waiting_for_delegate_input',
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === `end:delegate:${delegateId}:main-turn`)).toMatchObject({
      parentId: delegateId,
      finalMessage: 'The delegated analysis found three malicious domains.',
    });
  });

  it('recovers every unbound delegate input as a separate branch beneath the active delegate', () => {
    const delegateId = 'delegate:main-turn:delegate-call';
    const delegateTurnIds = ['delegate-turn-1', 'delegate-turn-2', 'delegate-turn-3'];
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        { id: 'main-user', sender: 'user', text: 'Start delegate loop', timestamp: '10:00', turnId: 'main-turn', source: 'main' },
        ...delegateTurnIds.map((turnId, index) => ({
          id: `user-${turnId}`,
          sender: 'user',
          text: `Delegate task ${index + 1}`,
          timestamp: `10:0${index + 1}`,
          turnId,
          source: 'delegate' as const,
        })),
      ],
      trace: [
        { id: 'main-accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'delegate-started',
          type: 'tool.started',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'delegate-call',
          delegateId,
          srcagent: 'threat-intel',
        },
        {
          id: 'delegate-entered',
          type: 'delegate.entered',
          timestamp: 3,
          turnId: 'main-turn',
          parentTurnId: 'main-turn',
          source: 'delegate',
          delegateId,
          srcagent: 'threat-intel',
        },
        ...delegateTurnIds.flatMap((turnId, index) => [
          {
            id: `accepted-${turnId}`,
            type: 'message.accepted' as const,
            timestamp: 4 + index * 3,
            turnId,
            source: 'delegate' as const,
            srcagent: 'threat-intel',
          },
          {
            id: `final-${turnId}`,
            type: 'message.completed' as const,
            timestamp: 5 + index * 3,
            turnId,
            source: 'delegate' as const,
            srcagent: 'threat-intel',
            messageId: `assistant-${turnId}`,
            content: `Delegate result ${index + 1}`,
          },
          {
            id: `waiting-${turnId}`,
            type: 'run.state' as const,
            timestamp: 6 + index * 3,
            turnId,
            source: 'delegate' as const,
            srcagent: 'threat-intel',
            state: 'waiting_for_delegate_input',
          },
        ]),
      ],
    });

    expect(graph.edges.filter((edge) => edge.from === delegateId).map((edge) => edge.to)).toEqual([
      'turn:delegate-turn-1',
      'turn:delegate-turn-2',
      'turn:delegate-turn-3',
    ]);
    delegateTurnIds.forEach((turnId, index) => {
      expect(graph.nodes.find((node) => node.id === `end:delegate:${delegateId}:${turnId}`)).toMatchObject({
        finalMessage: `Delegate result ${index + 1}`,
      });
    });
  });

  it('keeps direct turns isolated when one delegate exits and another enters the same main turn', () => {
    const firstDelegateId = 'delegate:main-turn:first-call';
    const secondDelegateId = 'delegate:main-turn:second-call';
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        { id: 'main', sender: 'user', text: 'Run two delegates', timestamp: '10:00', turnId: 'main-turn', source: 'main' },
        { id: 'first', sender: 'user', text: 'First follow-up', timestamp: '10:01', turnId: 'first-turn', source: 'delegate' },
        { id: 'second', sender: 'user', text: 'Second follow-up', timestamp: '10:02', turnId: 'second-turn', source: 'delegate' },
      ],
      trace: [
        { id: 'main-accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'first-start',
          type: 'tool.started',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'first-call',
          delegateId: firstDelegateId,
          srcagent: 'agent-one',
        },
        {
          id: 'first-enter',
          type: 'delegate.entered',
          timestamp: 3,
          turnId: 'main-turn',
          source: 'delegate',
          delegateId: firstDelegateId,
          srcagent: 'agent-one',
        },
        { id: 'first-accepted', type: 'message.accepted', timestamp: 4, turnId: 'first-turn', source: 'delegate', srcagent: 'agent-one' },
        { id: 'first-exit', type: 'delegate.exited', timestamp: 5, turnId: 'first-turn', source: 'delegate', delegateId: firstDelegateId, srcagent: 'agent-one' },
        {
          id: 'second-start',
          type: 'tool.started',
          timestamp: 6,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'second-call',
          delegateId: secondDelegateId,
          srcagent: 'agent-two',
        },
        {
          id: 'second-enter',
          type: 'delegate.entered',
          timestamp: 7,
          turnId: 'main-turn',
          source: 'delegate',
          delegateId: secondDelegateId,
          srcagent: 'agent-two',
        },
        { id: 'second-accepted', type: 'message.accepted', timestamp: 8, turnId: 'second-turn', source: 'delegate', srcagent: 'agent-two' },
      ],
    });

    expect(graph.nodes.find((node) => node.id === 'turn:first-turn')?.parentId).toBe(firstDelegateId);
    expect(graph.nodes.find((node) => node.id === 'turn:second-turn')?.parentId).toBe(secondDelegateId);
  });

  it('keeps actions inside a main turn sequential while delegate inputs branch', () => {
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          kind: 'chat',
          text: 'Investigate',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
      ],
      trace: [
        { id: 'accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'search',
          type: 'tool.completed',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'search',
          toolCallId: 'search-call',
        },
        {
          id: 'delegate',
          type: 'tool.started',
          timestamp: 3,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'aegis_a2a_delegate',
          toolCallId: 'delegate-call',
          delegateId: 'delegate:main-turn:delegate-call',
          srcagent: 'threat-intel',
        },
        {
          id: 'report',
          type: 'tool.completed',
          timestamp: 4,
          turnId: 'main-turn',
          source: 'main',
          toolName: 'report',
          toolCallId: 'report-call',
        },
        {
          id: 'idle',
          type: 'run.state',
          timestamp: 5,
          turnId: 'main-turn',
          source: 'main',
          state: 'idle',
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === 'tool:main-turn:search-call')?.parentId).toBe('turn:main-turn');
    expect(graph.nodes.find((node) => node.id === 'delegate:main-turn:delegate-call')?.parentId).toBe(
      'tool:main-turn:search-call',
    );
    expect(graph.nodes.find((node) => node.id === 'tool:main-turn:report-call')?.parentId).toBe(
      'delegate:main-turn:delegate-call',
    );
    expect(graph.nodes.find((node) => node.id === 'end:main:main-turn')?.parentId).toBe(
      'tool:main-turn:report-call',
    );
  });

  it('renders a failed terminal state as a stable completed trace', () => {
    const graph = projectSessionWorkflow({
      conversationId: 'conversation-1',
      title: 'Investigation',
      messages: [
        {
          id: 'user-main',
          sender: 'user',
          text: 'Inspect the host',
          timestamp: '10:00',
          turnId: 'main-turn',
          source: 'main',
        },
      ],
      trace: [
        { id: 'accepted', type: 'message.accepted', timestamp: 1, turnId: 'main-turn', source: 'main' },
        {
          id: 'failed',
          type: 'run.state',
          timestamp: 2,
          turnId: 'main-turn',
          source: 'main',
          state: 'error',
          reason: 'Connection refused',
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === 'end:main:main-turn')).toMatchObject({
      kind: 'end',
      label: 'Task failed',
      detail: 'Connection refused',
      status: 'error',
    });
    expect(graph.status).toBe('complete');
  });
});

describe('applyWorkflowSocketEvent', () => {
  it('binds an accepted event to its optimistic user message and deduplicates the event', () => {
    const conversation = {
      id: 'conversation-1',
      title: 'Investigation',
      timestamp: '10:00',
      messages: [
        {
          id: 'user-1',
          sender: 'user' as const,
          kind: 'chat' as const,
          text: 'Inspect the alert',
          timestamp: '10:00',
          clientMsgId: 'client-1',
        },
      ],
    };
    const event = {
      type: 'message.accepted',
      server_event_id: 'session-1:2',
      ts: 10,
      turn_id: 'turn-1',
      source: 'main' as const,
      client_msg_id: 'client-1',
    };

    const once = applyWorkflowSocketEvent(conversation, event);
    const twice = applyWorkflowSocketEvent(once, event);

    expect(twice.messages[0]).toMatchObject({ turnId: 'turn-1', source: 'main' });
    expect(twice.workflowTraceVersion).toBe(1);
    expect(twice.workflowTrace).toHaveLength(1);
    expect(twice.workflowTrace?.[0]).toMatchObject({
      id: 'session-1:2',
      type: 'message.accepted',
      turnId: 'turn-1',
      clientMsgId: 'client-1',
    });
  });

  it('associates delegate foreground inputs with the active delegate branch', () => {
    const initial = {
      id: 'conversation-1',
      title: 'Investigation',
      timestamp: '10:00',
      messages: [
        {
          id: 'delegate-input',
          sender: 'user' as const,
          kind: 'chat' as const,
          text: 'Inspect the headers',
          timestamp: '10:01',
          clientMsgId: 'client-delegate',
        },
      ],
    };
    const withDelegateTool = applyWorkflowSocketEvent(initial, {
      type: 'tool.started',
      server_event_id: 'session-1:3',
      ts: 11,
      turn_id: 'main-turn',
      source: 'main',
      tool_name: 'aegis_a2a_delegate',
      tool_call_id: 'delegate-call',
      args_preview: '{"agent_name":"threat-intel","task":"inspect headers"}',
    });
    const entered = applyWorkflowSocketEvent(withDelegateTool, {
      type: 'delegate.entered',
      server_event_id: 'session-1:4',
      ts: 12,
      turn_id: 'main-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
    });
    const accepted = applyWorkflowSocketEvent(entered, {
      type: 'message.accepted',
      server_event_id: 'session-1:5',
      ts: 13,
      turn_id: 'delegate-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
      client_msg_id: 'client-delegate',
    });

    const delegateId = 'delegate:main-turn:delegate-call';
    expect(accepted.workflowTrace?.[0]).toMatchObject({ delegateId, srcagent: 'threat-intel' });
    expect(accepted.workflowTrace?.[2]).toMatchObject({
      delegateId,
      parentTurnId: 'main-turn',
    });
    expect(accepted.messages[0]).toMatchObject({
      turnId: 'delegate-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
      workflowParentId: delegateId,
    });
  });

  it('ignores unknown events and does not reactivate a delegate after it exits', () => {
    const initial = {
      id: 'conversation-1',
      title: 'Investigation',
      timestamp: '10:00',
      messages: [],
    };
    const unchanged = applyWorkflowSocketEvent(initial, {
      type: 'unrelated.event',
      server_event_id: 'unknown',
    });
    expect(unchanged).toBe(initial);

    const started = applyWorkflowSocketEvent(initial, {
      type: 'tool.started',
      server_event_id: 'started',
      turn_id: 'main-turn',
      source: 'main',
      tool_name: 'aegis_a2a_delegate',
      tool_call_id: 'delegate-call',
    });
    const exited = applyWorkflowSocketEvent(started, {
      type: 'delegate.exited',
      server_event_id: 'exited',
      turn_id: 'main-turn',
      source: 'delegate',
    });
    const completed = applyWorkflowSocketEvent(exited, {
      type: 'tool.completed',
      server_event_id: 'completed',
      turn_id: 'main-turn',
      source: 'main',
      tool_name: 'aegis_a2a_delegate',
      tool_call_id: 'delegate-call',
    });
    const accepted = applyWorkflowSocketEvent(completed, {
      type: 'message.accepted',
      server_event_id: 'late-delegate-input',
      turn_id: 'delegate-turn',
      source: 'delegate',
    });

    expect(accepted.workflowTrace?.at(-1)?.delegateId).toBeUndefined();
  });

  it('records streamed and non-streamed final messages with active delegate context', () => {
    const initial = {
      id: 'conversation-1',
      title: 'Investigation',
      timestamp: '10:00',
      messages: [],
    };
    const started = applyWorkflowSocketEvent(initial, {
      type: 'tool.started',
      server_event_id: 'started',
      turn_id: 'main-turn',
      source: 'main',
      tool_name: 'aegis_a2a_delegate',
      tool_call_id: 'delegate-call',
    });
    const streamed = applyWorkflowSocketEvent(started, {
      type: 'message.stream.completed',
      server_event_id: 'streamed',
      turn_id: 'main-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
      message_id: 'delegate-stream',
    });
    const completed = applyWorkflowSocketEvent(streamed, {
      type: 'message.completed',
      server_event_id: 'completed',
      turn_id: 'delegate-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
      message_id: 'delegate-final',
      content: 'Final delegate result',
    });

    expect(completed.workflowTrace?.slice(-2)).toEqual([
      expect.objectContaining({
        type: 'message.stream.completed',
        delegateId: 'delegate:main-turn:delegate-call',
        messageId: 'delegate-stream',
      }),
      expect.objectContaining({
        type: 'message.completed',
        delegateId: 'delegate:main-turn:delegate-call',
        messageId: 'delegate-final',
        content: 'Final delegate result',
      }),
    ]);
  });
});
