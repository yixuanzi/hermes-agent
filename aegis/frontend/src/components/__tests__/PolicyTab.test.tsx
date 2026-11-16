import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PolicyTab from '../PolicyTab';
import { initialRules } from '../../data/mockData';

describe('PolicyTab', () => {
  it('shows global routing and Agent Policy sub-tabs', () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ policies: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    render(
      <PolicyTab
        busy={false}
        onCreate={vi.fn(async () => {})}
        onDelete={vi.fn(async () => {})}
        onRefresh={vi.fn(async () => {})}
        onUpdate={vi.fn(async () => {})}
        rules={initialRules}
      />,
    );

    const globalTab = screen.getByRole('button', { name: /global routing rules/i });
    const agentTab = screen.getByRole('button', { name: /agent policy/i });

    expect(globalTab.parentElement).not.toHaveClass('aegis-page-tabs--compact');
    expect(globalTab).toHaveAttribute('aria-pressed', 'true');
    expect(globalTab).toHaveClass('aegis-btn--selected');
    expect(agentTab).toHaveAttribute('aria-pressed', 'false');
    expect(
      screen.queryByText('Global Routing Rules (全局规则路由)'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Global Routing Rules' }),
    ).toHaveClass('text-cyan-400');
    expect(
      screen.getByText('Priority-ordered rules route matching requests to the configured agent.'),
    ).toHaveClass('text-slate-500');

    fireEvent.click(agentTab);

    expect(globalTab).toHaveAttribute('aria-pressed', 'false');
    expect(agentTab).toHaveAttribute('aria-pressed', 'true');
    expect(agentTab).toHaveClass('aegis-btn--selected');
    fetchSpy.mockRestore();
  });
});
