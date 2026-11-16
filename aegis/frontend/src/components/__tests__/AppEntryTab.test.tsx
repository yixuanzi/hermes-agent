import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppEntryTab from '../AppEntryTab';

const entries = [
  {
    subscription_id: 'subscription-1',
    subscription_no: 'SUB-001',
    subscription_status: 'active' as const,
    effective_to: '2027-08-01T00:00:00Z',
    product_service_code: 'AEGIS-MDR',
    product_service_name: 'Aegis Agentic MDR',
    entry_url: 'https://app.example.com',
    entry_source: 'app' as const,
  },
  {
    subscription_id: 'subscription-2',
    subscription_no: 'SUB-002',
    subscription_status: 'expiring' as const,
    effective_to: '2026-09-01T00:00:00Z',
    product_service_code: 'AEGIS-XTIP',
    product_service_name: 'Threat Intelligence',
    entry_url: 'https://service.example.com',
    entry_source: 'subscription' as const,
  },
  {
    subscription_id: 'subscription-3',
    subscription_no: 'SUB-003',
    subscription_status: 'active' as const,
    effective_to: '2027-01-01T00:00:00Z',
    product_service_code: 'AEGIS-PENTEST',
    product_service_name: 'Penetration Testing',
    entry_url: null,
    entry_source: null,
  },
];

const directory = {
  organization_code: 'XINGHAI-SH',
  entries,
};

describe('AppEntryTab', () => {
  afterEach(cleanup);

  it('renders organization entries in a four-column grid and opens configured entries in a new tab', async () => {
    render(<AppEntryTab directory={directory} loading={false} error="" loaded onRefresh={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByRole('heading', { name: 'App Entry' })).toBeInTheDocument();
    expect(screen.getByText(/XINGHAI-SH/)).toBeInTheDocument();
    expect(screen.getByText('Aegis Agentic MDR')).toBeInTheDocument();
    expect(screen.getByText('Threat Intelligence')).toBeInTheDocument();
    expect(screen.getByText('Entry not configured')).toBeInTheDocument();
    expect(screen.getByTestId('app-entry-card-disabled')).toHaveAttribute('aria-disabled', 'true');
    expect(document.querySelector('.xl\\:grid-cols-4')).toBeInTheDocument();

    const link = screen.getByRole('link', { name: 'Open Aegis Agentic MDR' });
    expect(link).toHaveAttribute('href', 'https://app.example.com');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('shows shared loading and error states and invokes the shared refresh callback', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<AppEntryTab directory={null} loading error="" loaded={false} onRefresh={onRefresh} />);

    expect(screen.getByText(/loading application directory/i)).toBeInTheDocument();
    rerender(<AppEntryTab directory={null} loading={false} error="portal unavailable" loaded={false} onRefresh={onRefresh} />);
    expect(screen.getByText('portal unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry directory/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
