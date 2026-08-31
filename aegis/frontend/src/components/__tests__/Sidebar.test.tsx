import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Sidebar from '../Sidebar';

describe('Sidebar user profile navigation', () => {
  afterEach(cleanup);

  it('keeps User Profile collapsed until opened, then navigates to User Manual', () => {
    const setActiveTab = vi.fn();
    render(<Sidebar activeTab="overview" setActiveTab={setActiveTab} />);

    expect(screen.queryByRole('button', { name: /user manual/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /user profile/i }));
    fireEvent.click(screen.getByRole('button', { name: /user manual/i }));

    expect(setActiveTab).toHaveBeenCalledWith('user_manual');
  });

  it('places App Entry after the complete User Profile section for every user', () => {
    const setActiveTab = vi.fn();
    const { container } = render(<Sidebar activeTab="overview" setActiveTab={setActiveTab} />);

    const userProfile = screen.getByRole('button', { name: /user profile/i });
    const appEntry = screen.getByRole('button', { name: /app entry/i });
    expect(userProfile.compareDocumentPosition(appEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(appEntry);
    expect(setActiveTab).toHaveBeenCalledWith('app_entry');
    expect(container.querySelector('[aria-label="App Entry"]')).toBeInTheDocument();
  });

  it('locks System Integrity outside the scrolling menu and toggles an icon navigation rail', () => {
    const setActiveTab = vi.fn();
    const { container } = render(<Sidebar activeTab="chat" setActiveTab={setActiveTab} chatAttentionCount={2} isAdmin />);

    const sidebar = container.querySelector('#sidebar-container');
    const menuScrollRegion = container.querySelector('[data-testid="sidebar-menu-scroll-region"]');
    const integrityFooter = container.querySelector('[data-testid="sidebar-integrity-footer"]');

    expect(sidebar).toHaveClass('overflow-hidden');
    expect(menuScrollRegion).toHaveClass('min-h-0', 'flex-1', 'overflow-y-auto');
    expect(integrityFooter).toHaveClass('shrink-0');
    expect(container.querySelector('[data-testid="sidebar-hub-row"]')).toContainElement(
      container.querySelector('[aria-label="Collapse navigation"]'),
    );

    fireEvent.click(container.querySelector('[aria-label="Collapse navigation"]') as HTMLElement);
    expect(sidebar).toHaveClass('w-16');
    expect(container.querySelector('[aria-label="Expand navigation"]')).toHaveAttribute('aria-expanded', 'false');
    expect(container.querySelector('[aria-label="Chat attention count: 2"]')).toBeInTheDocument();

    fireEvent.click(container.querySelector('[aria-label="Agent Orchestration"]') as HTMLElement);
    expect(setActiveTab).toHaveBeenCalledWith('orchestration');

    fireEvent.click(container.querySelector('[aria-label="User Profile"]') as HTMLElement);
    expect(sidebar).toHaveClass('w-64');
    expect(container.querySelector('#user-profile-menu')).toBeInTheDocument();
  });

  it('restores the expanded sidebar when mounted again', () => {
    const setActiveTab = vi.fn();
    const firstRender = render(<Sidebar activeTab="overview" setActiveTab={setActiveTab} />);

    fireEvent.click(firstRender.container.querySelector('[aria-label="Collapse navigation"]') as HTMLElement);
    expect(firstRender.container.querySelector('#sidebar-container')).toHaveClass('w-16');

    firstRender.unmount();
    const secondRender = render(<Sidebar activeTab="overview" setActiveTab={setActiveTab} />);
    expect(secondRender.container.querySelector('#sidebar-container')).toHaveClass('w-64');
    expect(secondRender.container.querySelector('[aria-label="Collapse navigation"]')).toBeInTheDocument();
  });
});
