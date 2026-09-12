import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AgentTab from '../AgentTab';
import type { Agent, AgentDraft } from '../../types';

const agents: Agent[] = [
  { id: 'active', name: 'Active agent', type: 'agent', description: 'Running checks', status: 'Active', tasksCount: 1, lastUpdated: 'now' },
  { id: 'idle', name: 'Idle agent', type: 'agent', description: 'Waiting for work', status: 'Idle', tasksCount: 0, lastUpdated: 'now' },
  { id: 'offline', name: 'Offline agent', type: 'agent', description: 'Unavailable', status: 'Offline', tasksCount: 0, lastUpdated: 'now' },
];

describe('AgentTab status presentation', () => {
  afterEach(() => cleanup());

  it('uses semantic status badges and indicators for every runtime state', () => {
    render(
      <AgentTab
        agents={agents}
        busy={false}
        onCreate={vi.fn(async () => {})}
        onDelete={vi.fn(async () => {})}
        onRefresh={vi.fn(async () => {})}
        onUpdate={vi.fn(async () => {})}
      />,
    );

    expect(screen.getByText('Active', { selector: '.aegis-status-badge' })).toHaveClass('aegis-status-badge--success');
    expect(screen.getByText('Idle', { selector: '.aegis-status-badge' })).toHaveClass('aegis-status-badge--warning');
    expect(screen.getByText('Offline', { selector: '.aegis-status-badge' })).toHaveClass('aegis-status-badge--danger');
    expect(document.querySelectorAll('.aegis-status-indicator--success')).toHaveLength(1);
    expect(document.querySelectorAll('.aegis-status-indicator--warning')).toHaveLength(1);
    expect(document.querySelectorAll('.aegis-status-indicator--danger')).toHaveLength(1);
  });
});

describe('AgentTab auth header configuration', () => {
  afterEach(() => cleanup());

  it('defaults to a single header row and submits multiple configured headers', async () => {
    const onCreate = vi.fn(async (_draft: AgentDraft) => {});

    render(
      <AgentTab
        agents={[]}
        busy={false}
        onCreate={onCreate}
        onDelete={vi.fn(async () => {})}
        onRefresh={vi.fn(async () => {})}
        onUpdate={vi.fn(async () => {})}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /注册智能体/i }));
    expect(screen.getByRole('textbox', { name: '验证头字段名 1' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '验证头字段名 2' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/e\.g\. threat-intel/i), {
      target: { value: 'multi-header-agent' },
    });
    fireEvent.change(screen.getByPlaceholderText(/http:\/\/127\.0\.0\.1:9086\/a2a/i), {
      target: { value: 'http://127.0.0.1:9090/a2a' },
    });
    fireEvent.change(screen.getByPlaceholderText(/审计服务器特权指令偏差/i), {
      target: { value: 'Agent with two headers' },
    });

    fireEvent.change(screen.getByRole('textbox', { name: '验证头字段名 1' }), {
      target: { value: 'Authorization' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: '验证头字段值 1' }), {
      target: { value: 'Bearer abc' },
    });

    fireEvent.click(screen.getByRole('button', { name: /添加验证头/i }));
    expect(screen.getByRole('textbox', { name: '验证头字段名 2' })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: '验证头字段名 2' }), {
      target: { value: 'X-Api-Key' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: '验证头字段值 2' }), {
      target: { value: 'secret-key' },
    });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onCreate.mock.calls[0][0].headers).toEqual([
      { key: 'Authorization', value: 'Bearer abc' },
      { key: 'X-Api-Key', value: 'secret-key' },
    ]);
  });

  it('removes an individual header row without disturbing the others', () => {
    render(
      <AgentTab
        agents={[]}
        busy={false}
        onCreate={vi.fn(async () => {})}
        onDelete={vi.fn(async () => {})}
        onRefresh={vi.fn(async () => {})}
        onUpdate={vi.fn(async () => {})}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /注册智能体/i }));
    fireEvent.click(screen.getByRole('button', { name: /添加验证头/i }));
    fireEvent.change(screen.getByRole('textbox', { name: '验证头字段名 1' }), {
      target: { value: 'Authorization' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: '验证头字段名 2' }), {
      target: { value: 'X-Api-Key' },
    });

    fireEvent.click(screen.getByRole('button', { name: '删除验证头 1' }));

    expect(screen.queryByRole('textbox', { name: '验证头字段名 2' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '验证头字段名 1' })).toHaveValue('X-Api-Key');
  });
});
