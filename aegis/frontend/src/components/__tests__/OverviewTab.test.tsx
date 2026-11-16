import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import OverviewTab from '../OverviewTab';
import { OverviewStats } from '../../types';

const stats: OverviewStats = {
  window_start: '2026-07-15T00:00:00.000000Z',
  window_end: '2026-07-22T00:00:00.000000Z',
  executing_agent_count: 5,
  source_platform_count: 3,
  active_user_count: 12,
  delegation_total: 24,
  success_count: 16,
  success_rate: 2 / 3,
  status_counts: { succ: 16, fail: 5, auth_denied: 3 },
  comparison: {
    previous_delegation_total: 12,
    delegation_volume_change_percent: 100,
    previous_success_rate: 0.5,
    success_rate_change_percentage_points: 16.6667,
  },
};

afterEach(cleanup);

describe('OverviewTab delegation metrics', () => {
  it('renders all five API-backed delegation metrics and comparison details', () => {
    render(
      <OverviewTab
        agents={[]}
        currentUtcTime="2026-07-22 00:00:00"
        setTab={vi.fn()}
        stats={stats}
        statsError=""
        topology={null}
        topologyError=""
        appEntries={[]}
        appEntriesLoading={false}
        appEntriesError=""
        appEntriesLoaded={false}
      />,
    );

    expect(screen.getByText('Executing Task Agents')).toBeInTheDocument();
    expect(screen.getByText('Source Platforms')).toBeInTheDocument();
    expect(screen.getByText('Active Users')).toBeInTheDocument();
    expect(screen.getByText('Delegations Started')).toBeInTheDocument();
    expect(screen.getByText('Aegis Delegation Health (7D)')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('24')).toBeInTheDocument();
    expect(screen.getByText('↑ 100.0% vs previous 7 days')).toBeInTheDocument();
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.getByText('succ 16')).toBeInTheDocument();
    expect(screen.getByText('fail 5')).toBeInTheDocument();
    expect(screen.getByText('denied 3')).toBeInTheDocument();
    expect(screen.getByText('+16.7 pp vs previous 7 days')).toBeInTheDocument();
  });

  it('uses a neutral no-data state when no delegation was recorded', () => {
    render(
      <OverviewTab
        agents={[]}
        currentUtcTime="2026-07-22 00:00:00"
        setTab={vi.fn()}
        stats={{
          ...stats,
          delegation_total: 0,
          success_count: 0,
          success_rate: null,
          status_counts: { succ: 0, fail: 0, auth_denied: 0 },
          comparison: {
            previous_delegation_total: 0,
            delegation_volume_change_percent: null,
            previous_success_rate: null,
            success_rate_change_percentage_points: null,
          },
        }}
        statsError=""
        topology={null}
        topologyError=""
        appEntries={[]}
        appEntriesLoading={false}
        appEntriesError=""
        appEntriesLoaded={false}
      />,
    );

    expect(screen.getByText('No delegations')).toBeInTheDocument();
    expect(screen.getByText('No delegate audits were recorded in this window.')).toBeInTheDocument();
    expect(screen.getByText('No prior baseline')).toBeInTheDocument();
  });
});
