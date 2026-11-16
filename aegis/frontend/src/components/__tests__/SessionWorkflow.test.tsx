import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SessionWorkflow from '../SessionWorkflow';
import { Conversation } from '../../types';

afterEach(() => cleanup());

function completedConversation(): Conversation {
  return {
    id: 'conversation-1',
    title: 'Phishing Investigation',
    timestamp: '10:00',
    messages: [
      {
        id: 'user-1',
        sender: 'user',
        kind: 'chat',
        text: 'Investigate the suspicious phishing message and inspect every linked domain',
        timestamp: '10:00',
        turnId: 'turn-1',
        source: 'main',
      },
      {
        id: 'assistant-1',
        sender: 'aegis',
        kind: 'chat',
        text: 'The investigation is complete with one confirmed phishing domain.',
        timestamp: '10:01',
        turnId: 'turn-1',
        source: 'main',
      },
    ],
    workflowTraceVersion: 1,
    workflowTrace: [
      {
        id: 'accepted',
        type: 'message.accepted',
        timestamp: 1,
        turnId: 'turn-1',
        source: 'main',
      },
      {
        id: 'tool',
        type: 'tool.completed',
        timestamp: 2,
        turnId: 'turn-1',
        source: 'main',
        toolName: 'terminal',
        toolCallId: 'tool-1',
        argsPreview: '{"cmd":"pwd"}',
        resultPreview: '/Users/demo',
      },
      {
        id: 'final',
        type: 'message.completed',
        timestamp: 2.5,
        turnId: 'turn-1',
        source: 'main',
        messageId: 'assistant-1',
        content: 'The investigation is complete with one confirmed phishing domain.',
      },
      {
        id: 'idle',
        type: 'run.state',
        timestamp: 3,
        turnId: 'turn-1',
        source: 'main',
        state: 'idle',
      },
    ],
  };
}

function longToolConversation(id = 'long-conversation', toolCount = 8): Conversation {
  return {
    id,
    title: 'Long tool investigation',
    timestamp: '10:00',
    messages: [
      {
        id: `user-${id}`,
        sender: 'user',
        text: 'Run the complete investigation chain',
        timestamp: '10:00',
        turnId: 'long-turn',
        source: 'main',
      },
    ],
    workflowTraceVersion: 1,
    workflowTrace: [
      {
        id: `accepted-${id}`,
        type: 'message.accepted',
        timestamp: 1,
        turnId: 'long-turn',
        source: 'main',
      },
      ...Array.from({ length: toolCount }, (_, index) => ({
        id: `tool-event-${id}-${index + 1}`,
        type: 'tool.completed' as const,
        timestamp: index + 2,
        turnId: 'long-turn',
        source: 'main' as const,
        toolName: `tool-${index + 1}`,
        toolCallId: `tool-${index + 1}`,
      })),
      {
        id: `idle-${id}`,
        type: 'run.state',
        timestamp: toolCount + 3,
        turnId: 'long-turn',
        source: 'main',
        state: 'idle',
      },
    ],
  };
}

describe('SessionWorkflow', () => {
  it('collapses exactly four consecutive main tools at the inclusive threshold', () => {
    render(
      <SessionWorkflow
        conversation={longToolConversation('four-main-tools', 4)}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('+2 tools')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-1')).toBeInTheDocument();
    expect(screen.queryByTestId('workflow-node-tool:long-turn:tool-2')).not.toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-4')).toBeInTheDocument();
    expect(screen.getByText('6 ACTIONS · 4 TOOLS')).toBeInTheDocument();
  });

  it('progressively expands long tool chains without opening node details', () => {
    render(
      <SessionWorkflow
        conversation={longToolConversation()}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('+6 tools')).toBeInTheDocument();
    expect(screen.queryByTestId('workflow-node-tool:long-turn:tool-2')).not.toBeInTheDocument();
    expect(screen.getByText('10 ACTIONS · 8 TOOLS')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /show next 3 of 6 hidden tools/i }));
    expect(screen.queryByText('NODE DETAILS')).not.toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-2')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-4')).toBeInTheDocument();
    expect(screen.getByText('+3 tools')).toBeInTheDocument();

    fireEvent.keyDown(screen.getByRole('button', { name: /show next 3 of 3 hidden tools/i }), {
      key: 'Enter',
    });
    expect(screen.queryByText('+3 tools')).not.toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-7')).toBeInTheDocument();
  });

  it('keeps viewport state while expanding and resets folding for a new session', async () => {
    const initialConversation = longToolConversation();
    const { rerender } = render(
      <SessionWorkflow
        conversation={initialConversation}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const canvas = screen.getByTestId('workflow-canvas');
    fireEvent.click(screen.getByRole('button', { name: /zoom in workflow/i }));
    const scaleBeforeExpansion = canvas.getAttribute('data-scale');
    fireEvent.click(screen.getByRole('button', { name: /show next 3 of 6 hidden tools/i }));
    expect(canvas).toHaveAttribute('data-scale', scaleBeforeExpansion);

    const idle = initialConversation.workflowTrace?.at(-1);
    const liveExtendedConversation: Conversation = {
      ...initialConversation,
      workflowTrace: [
        ...(initialConversation.workflowTrace?.slice(0, -1) || []),
        {
          id: 'live-tool-9',
          type: 'tool.completed',
          timestamp: 10.5,
          turnId: 'long-turn',
          source: 'main',
          toolName: 'tool-9',
          toolCallId: 'tool-9',
        },
        ...(idle ? [idle] : []),
      ],
    };
    rerender(
      <SessionWorkflow
        conversation={liveExtendedConversation}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('+4 tools')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-2')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:long-turn:tool-9')).toBeInTheDocument();
    expect(
      screen.getByTestId('workflow-edge-base-tool:long-turn:tool-9->end:main:long-turn'),
    ).toBeInTheDocument();
    expect(canvas).toHaveAttribute('data-scale', scaleBeforeExpansion);

    rerender(
      <SessionWorkflow
        conversation={longToolConversation('next-conversation')}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText('+6 tools')).toBeInTheDocument());
    expect(screen.queryByTestId('workflow-node-tool:long-turn:tool-2')).not.toBeInTheDocument();
  });

  it('renders semantic node halos and layered directional edges', () => {
    render(
      <SessionWorkflow
        conversation={completedConversation()}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getAllByTestId('workflow-node-halo-root')).toHaveLength(1);
    expect(screen.getAllByTestId('workflow-node-halo-input')).toHaveLength(1);
    expect(screen.getAllByTestId('workflow-node-halo-tool')).toHaveLength(1);
    expect(screen.getAllByTestId('workflow-node-halo-end')).toHaveLength(1);
    const toolNode = screen.getByTestId('workflow-node-tool:turn-1:tool-1');
    expect(toolNode).toHaveAttribute('tabindex', '0');
    expect(screen.getByTestId('workflow-node-focus-tool')).toHaveClass('workflow-node-focus-ring');
    expect(document.querySelectorAll('[data-edge-layer="base"]')).toHaveLength(3);
    expect(document.querySelectorAll('[data-edge-layer="flow"]')).toHaveLength(3);
    document.querySelectorAll('[data-edge-layer="flow"]').forEach((edge) => {
      expect(edge).toHaveClass('workflow-edge-flow');
      expect(edge).toHaveAttribute('vector-effect', 'non-scaling-stroke');
    });
  });

  it('keeps node labels compact and reveals full details only after selection', () => {
    render(
      <SessionWorkflow
        conversation={completedConversation()}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Investigate the suspicio…')).toBeInTheDocument();
    expect(
      screen.queryByText('Investigate the suspicious phishing message and inspect every linked domain'),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /select investigate the suspicious/i }));

    expect(screen.getByText('NODE DETAILS')).toBeInTheDocument();
    expect(
      screen.getByText('Investigate the suspicious phishing message and inspect every linked domain'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-turn:turn-1')).toHaveAttribute('data-highlighted', 'true');
    expect(screen.getByTestId('workflow-node-session:conversation-1')).toHaveAttribute(
      'data-highlighted',
      'true',
    );
  });

  it('supports zoom controls and fullscreen and close actions', async () => {
    const onFullscreenChange = vi.fn();
    const onClose = vi.fn();
    render(
      <SessionWorkflow
        conversation={completedConversation()}
        fullscreen={false}
        onFullscreenChange={onFullscreenChange}
        onClose={onClose}
      />,
    );

    const canvas = screen.getByTestId('workflow-canvas');
    const initialScale = Number(canvas.getAttribute('data-scale'));
    fireEvent.click(screen.getByRole('button', { name: /zoom in workflow/i }));
    await waitFor(() => {
      expect(Number(canvas.getAttribute('data-scale'))).toBeGreaterThan(initialScale);
    });

    expect(screen.getByRole('button', { name: /fit workflow to view/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reset workflow view/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /enter workflow fullscreen/i }));
    expect(onFullscreenChange).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole('button', { name: /close workflow visualization/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('keeps trackpad scrolling inside node details from zooming the workflow canvas', async () => {
    render(
      <SessionWorkflow
        conversation={completedConversation()}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const canvas = screen.getByTestId('workflow-canvas');
    fireEvent.click(screen.getByTestId('workflow-node-end:main:turn-1'));
    const detailScroll = screen.getByTestId('workflow-node-details-scroll');
    const scaleBeforeDetailWheel = canvas.getAttribute('data-scale');

    fireEvent.wheel(detailScroll, { deltaY: 100, clientX: 300, clientY: 300 });
    expect(canvas).toHaveAttribute('data-scale', scaleBeforeDetailWheel);
    expect(screen.getByText('FINAL MESSAGE')).toBeInTheDocument();
    expect(
      screen.getByText('The investigation is complete with one confirmed phishing domain.'),
    ).toBeInTheDocument();

    fireEvent.wheel(canvas, { deltaY: -100, clientX: 300, clientY: 300 });
    await waitFor(() => {
      expect(canvas.getAttribute('data-scale')).not.toBe(scaleBeforeDetailWheel);
    });
  });

  it('pans the canvas and clears a selected node when the user clicks blank canvas', async () => {
    const onClose = vi.fn();
    render(
      <SessionWorkflow
        conversation={completedConversation()}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={onClose}
      />,
    );

    const canvas = screen.getByTestId('workflow-canvas');
    const initialX = Number(canvas.getAttribute('data-offset-x'));
    fireEvent.pointerDown(canvas, { pointerId: 7, clientX: 30, clientY: 40 });
    fireEvent.pointerMove(canvas, { pointerId: 7, clientX: 90, clientY: 70 });
    fireEvent.pointerUp(canvas, { pointerId: 7 });
    await waitFor(() => {
      expect(Number(canvas.getAttribute('data-offset-x'))).toBeGreaterThan(initialX);
    });

    fireEvent.click(screen.getByRole('button', { name: /select investigate the suspicious/i }));
    expect(screen.getByText('NODE DETAILS')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Session execution tree'));
    expect(screen.queryByText('NODE DETAILS')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('preserves selection and a user-adjusted viewport as live events extend the same session', async () => {
    const initialConversation = completedConversation();
    const { rerender } = render(
      <SessionWorkflow
        conversation={initialConversation}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const canvas = screen.getByTestId('workflow-canvas');
    await waitFor(() => expect(canvas).not.toHaveAttribute('data-scale', '1.00'));
    fireEvent.click(screen.getByRole('button', { name: /zoom in workflow/i }));
    fireEvent.click(screen.getByRole('button', { name: /select investigate the suspicious/i }));
    const adjustedScale = canvas.getAttribute('data-scale');

    rerender(
      <SessionWorkflow
        conversation={{
          ...initialConversation,
          workflowTrace: [
            ...(initialConversation.workflowTrace || []),
            {
              id: 'late-tool',
              type: 'tool.completed',
              timestamp: 4,
              turnId: 'turn-1',
              source: 'main',
              toolName: 'report_builder',
              toolCallId: 'tool-2',
            },
          ],
        }}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByText('NODE DETAILS')).toBeInTheDocument());
    expect(canvas).toHaveAttribute('data-scale', adjustedScale);
    expect(screen.getByTestId('workflow-node-tool:turn-1:tool-2')).toBeInTheDocument();
  });

  it('renders a partial static tree from legacy cached messages without a trace', () => {
    const legacyConversation: Conversation = {
      id: 'legacy-conversation',
      title: 'Legacy Investigation',
      timestamp: '10:00',
      messages: [
        {
          id: 'legacy-user',
          sender: 'user',
          kind: 'chat',
          text: 'Review the legacy alert',
          timestamp: '10:00',
        },
        {
          id: 'legacy-tools',
          sender: 'aegis',
          kind: 'main-tools',
          text: 'Main orchestration activity',
          timestamp: '10:01',
          turnId: 'legacy-turn-id',
          source: 'main',
          chainSteps: [
            {
              id: 'legacy-tool',
              agentName: 'terminal',
              type: 'vip_tool',
              status: 'Completed',
              message: 'done',
              timestamp: '10:01',
            },
          ],
        },
        {
          id: 'legacy-delegate-tools',
          sender: 'aegis',
          kind: 'delegate-tools',
          text: 'Delegate Tool Activity',
          timestamp: '10:02',
          turnId: 'legacy-turn-id',
          source: 'delegate',
          srcagent: 'threat-intel',
          delegateTools: [
            {
              id: 'legacy-delegate-tool',
              toolName: 'ioc_lookup',
              argsPreview: 'example.com',
              resultPreview: 'malicious',
              status: 'completed',
            },
          ],
        },
      ],
    };

    render(
      <SessionWorkflow
        conversation={legacyConversation}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('PARTIAL TRACE')).toBeInTheDocument();
    expect(screen.getByText('Review the legacy alert')).toBeInTheDocument();
    expect(screen.getByText('terminal')).toBeInTheDocument();
    expect(screen.getByText('threat-intel')).toBeInTheDocument();
    expect(screen.getByText('ioc_lookup')).toBeInTheDocument();
  });

  it('does not invent a legacy node for a newly submitted message awaiting acceptance', () => {
    render(
      <SessionWorkflow
        conversation={{
          id: 'live-conversation',
          title: 'Live Investigation',
          timestamp: '10:00',
          messages: [
            {
              id: 'optimistic-user',
              sender: 'user',
              kind: 'chat',
              text: 'New live turn',
              timestamp: '10:00',
              clientMsgId: 'client-live',
            },
          ],
        }}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('AWAITING FIRST TURN')).toBeInTheDocument();
    expect(screen.queryByText('New live turn')).not.toBeInTheDocument();
    expect(screen.queryByText('PARTIAL TRACE')).not.toBeInTheDocument();
  });
});
