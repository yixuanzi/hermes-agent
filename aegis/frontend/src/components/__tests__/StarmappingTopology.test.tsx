import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import StarmappingTopology from '../StarmappingTopology';
import type { StarmappingTopology as StarmappingTopologyData } from '../../types';

const topology: StarmappingTopologyData = {
  schema_version: '1.0.0',
  updated_at: '2026-07-24',
  center: {
    id: 'aegis', layer: 'center', name: 'Aegis', symbol: 'aegis-connection',
    role: 'Security & Operations Orchestration Core', description: '统一编排中枢。',
    capabilities: ['agent-delegation'], layout: { x: 0.5, y: 0.5, radius: 'core' },
  },
  agents: [{
    id: 'ai-soc', layer: 'agent_ring', business_domain: 'AI-SOC', product_service_code: 'WORKAGENT-AI-SOC', name: 'Argus', display_name: 'AI-SOC · Argus', marketing_name: '[ Argus ]', symbol: 'argus-eyes',
    cultural_origin: '希腊神话·百眼巨人', business_fit: '全天候、零死角威胁监控与感知', role: '安全运营',
    layout: { ring_position: 0, angle_degrees: 270, radius: 'agent' }, runtime: { status: 'active', source: 'a2a_registry' },
    star_nodes: Array.from({ length: 10 }, (_, index) => ({
      id: `argus-star-${index}`, layer: 'star_field' as const, kind: index === 0 ? 'tool' as const : 'data' as const,
      name: index === 0 ? 'SIEM 检索与关联' : `安全数据 ${index}`, integration: index === 0 ? 'Splunk API' : 'Data lake API',
      purpose: index === 0 ? '检索、关联和回溯安全事件' : '提供安全证据', required: true,
    })),
  }],
  edges: [
    { source: 'aegis', target: 'ai-soc', mode: 'orchestrates' },
    ...Array.from({ length: 10 }, (_, index) => ({ source: 'ai-soc', target: `argus-star-${index}`, mode: 'requires' as const })),
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('StarmappingTopology', () => {
  it('shows and illuminates API-backed node details on hover', () => {
    render(<StarmappingTopology topology={topology} error="" appEntries={[]} appEntriesLoading={false} appEntriesError="" appEntriesLoaded={false} />);

    const background = screen.getByTestId('star-map-background');
    expect(background).toBeInTheDocument();
    expect(background.tagName).toBe('CANVAS');
    expect(background).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByRole('img', { name: 'Aegis three-layer orchestration topology' })).toHaveAttribute('preserveAspectRatio', 'xMidYMid meet');
    expect(screen.getByTestId('topology-symbol-aegis')).toBeInTheDocument();
    expect(screen.getByTestId('topology-symbol-ai-soc')).toBeInTheDocument();
    expect(screen.getByTestId('topology-core-halo')).toHaveClass('starmapping-core-halo');
    expect(screen.getByTestId('topology-core-orbit')).toHaveClass('starmapping-core-orbit');
    expect(screen.getByTestId('topology-agent-orbit-ai-soc')).toHaveClass('starmapping-agent-orbit');
    expect(screen.getByTestId('topology-agent-scout-ai-soc')).toHaveClass('starmapping-agent-scout');
    expect(screen.getAllByTestId('topology-flow')).toHaveLength(1);
    const twinkleNodes = screen.getAllByTestId('topology-twinkle');
    expect(twinkleNodes).toHaveLength(2);
    expect(twinkleNodes.every((node) => node.getAttribute('filter') === 'url(#star-map-glow)')).toBe(true);
    expect(twinkleNodes.every((node) => node.getAttribute('style')?.includes('animation-name: starmapping-star-twinkle'))).toBe(true);
    const twinkleDurations = twinkleNodes.map((node) => Number.parseFloat(node.getAttribute('style')?.match(/animation-duration: ([\d.]+)s/)?.[1] || '0'));
    expect(twinkleDurations.every((duration) => duration >= 1.42 && duration <= 1.58)).toBe(true);

    const agent = screen.getByLabelText(/AI-SOC · Argus/);
    fireEvent.pointerEnter(agent, { clientX: 100, clientY: 100 });
    expect(screen.getByText('全天候、零死角威胁监控与感知')).toBeInTheDocument();
    expect(screen.getByText('ACTIVE')).toBeInTheDocument();

    const star = screen.getByLabelText(/SIEM 检索与关联/);
    fireEvent.pointerEnter(star, { clientX: 220, clientY: 180 });
    expect(screen.getByText('检索、关联和回溯安全事件')).toBeInTheDocument();
    expect(screen.getByText(/Splunk API · REQUIRED/)).toBeInTheDocument();
  });

  it('opens a matching application entry from click, Enter, and Space activation', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const appEntries = [{
      subscription_id: 'subscription-1',
      subscription_no: 'SUB-001',
      subscription_status: 'active' as const,
      effective_to: '2027-08-01T00:00:00Z',
      product_service_code: ' workagent-ai-soc ',
      product_service_name: 'Argus Work Agent',
      entry_url: 'https://workagent.example.com',
      entry_source: 'app' as const,
    }];

    render(<StarmappingTopology topology={topology} error="" appEntries={appEntries} appEntriesLoading={false} appEntriesError="" appEntriesLoaded />);
    const agent = screen.getByLabelText(/AI-SOC · Argus/);

    fireEvent.click(agent);
    fireEvent.keyDown(agent, { key: 'Enter' });
    fireEvent.keyDown(agent, { key: ' ' });

    expect(openSpy).toHaveBeenCalledTimes(3);
    expect(openSpy).toHaveBeenLastCalledWith('https://workagent.example.com', '_blank', 'noopener,noreferrer');
    expect(screen.getByText('PRODUCT SERVICE · WORKAGENT-AI-SOC')).toBeInTheDocument();
    expect(screen.getByText('ENTRY READY')).toBeInTheDocument();
  });

  it.each([
    {
      label: 'no matching entry',
      appEntries: [],
      appEntriesLoading: false,
      appEntriesError: '',
      appEntriesLoaded: true,
      status: 'ENTRY UNAVAILABLE',
    },
    {
      label: 'an empty entry URL',
      appEntries: [{
        subscription_id: 'subscription-1', subscription_no: 'SUB-001', subscription_status: 'active' as const,
        effective_to: '2027-08-01T00:00:00Z', product_service_code: 'WORKAGENT-AI-SOC', product_service_name: 'Argus',
        entry_url: '  ', entry_source: 'app' as const,
      }],
      appEntriesLoading: false,
      appEntriesError: '',
      appEntriesLoaded: true,
      status: 'ENTRY UNAVAILABLE',
    },
    {
      label: 'a loading directory',
      appEntries: [{
        subscription_id: 'subscription-1', subscription_no: 'SUB-001', subscription_status: 'active' as const,
        effective_to: '2027-08-01T00:00:00Z', product_service_code: 'WORKAGENT-AI-SOC', product_service_name: 'Argus',
        entry_url: 'https://workagent.example.com', entry_source: 'app' as const,
      }],
      appEntriesLoading: true,
      appEntriesError: '',
      appEntriesLoaded: false,
      status: 'ENTRY UNAVAILABLE · DIRECTORY LOADING',
    },
    {
      label: 'a failed directory request',
      appEntries: [{
        subscription_id: 'subscription-1', subscription_no: 'SUB-001', subscription_status: 'active' as const,
        effective_to: '2027-08-01T00:00:00Z', product_service_code: 'WORKAGENT-AI-SOC', product_service_name: 'Argus',
        entry_url: 'https://workagent.example.com', entry_source: 'app' as const,
      }],
      appEntriesLoading: false,
      appEntriesError: 'Portal unavailable',
      appEntriesLoaded: false,
      status: 'ENTRY UNAVAILABLE · DIRECTORY ERROR',
    },
  ])('keeps the tooltip and does not open for $label', ({ appEntries, appEntriesLoading, appEntriesError, appEntriesLoaded, status }) => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    render(<StarmappingTopology topology={topology} error="" appEntries={appEntries} appEntriesLoading={appEntriesLoading} appEntriesError={appEntriesError} appEntriesLoaded={appEntriesLoaded} />);
    const agent = screen.getByLabelText(/AI-SOC · Argus/);
    fireEvent.click(agent);

    expect(openSpy).not.toHaveBeenCalled();
    expect(screen.getByText('全天候、零死角威胁监控与感知')).toBeInTheDocument();
    expect(screen.getByText(status)).toBeInTheDocument();
  });

  it('requests native fullscreen mode from the topology control', () => {
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
      configurable: true,
      value: requestFullscreen,
    });
    render(<StarmappingTopology topology={topology} error="" appEntries={[]} appEntriesLoading={false} appEntriesError="" appEntriesLoaded={false} />);

    fireEvent.click(screen.getByRole('button', { name: 'Enter topology fullscreen' }));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
  });

  it('splits each 30 percent selection into overlapping waves without a dark gap', () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0.25);
    render(<StarmappingTopology topology={topology} error="" appEntries={[]} appEntriesLoading={false} appEntriesError="" appEntriesLoaded={false} />);

    const firstWave = screen.getAllByTestId('topology-twinkle').map((node) => node.getAttribute('data-star-id'));
    expect(firstWave).toHaveLength(2);

    act(() => vi.advanceTimersByTime(800));
    const fullFirstBatch = screen.getAllByTestId('topology-twinkle').map((node) => node.getAttribute('data-star-id'));
    expect(fullFirstBatch).toHaveLength(3);
    const lingeringSecondWave = fullFirstBatch.filter((id) => !firstWave.includes(id));
    expect(lingeringSecondWave).toHaveLength(1);

    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getAllByTestId('topology-twinkle').map((node) => node.getAttribute('data-star-id'))).toEqual(lingeringSecondWave);

    act(() => vi.advanceTimersByTime(200));
    const bridgeBatch = screen.getAllByTestId('topology-twinkle').map((node) => node.getAttribute('data-star-id'));
    expect(bridgeBatch).toHaveLength(3);
    expect(bridgeBatch).toEqual(expect.arrayContaining(lingeringSecondWave));
    expect(bridgeBatch.filter((id) => !lingeringSecondWave.includes(id))).toHaveLength(2);
  });
});
