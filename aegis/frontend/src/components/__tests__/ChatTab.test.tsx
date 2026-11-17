import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatTab from '../ChatTab';
import { AegisChatProvider } from '../../lib/chatRuntime';
import { getDrawerTabsStorageKey, saveCachedDrawerTabs } from '../../lib/chatDrawerTabs';

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 1;
  sent: string[] = [];
  url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.onopen?.();
    });
  }

  send(payload: string) {
    this.sent.push(payload);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  emit(payload: unknown) {
    this.onmessage?.({
      data: JSON.stringify(payload),
    } as MessageEvent<string>);
  }
}

function getComposer(): HTMLDivElement {
  return screen.getByRole('combobox', { name: 'Chat message' }) as HTMLDivElement;
}

function setComposerText(composer: HTMLDivElement, text: string) {
  composer.textContent = text;
  fireEvent.input(composer);
}

describe('ChatTab', () => {
  const OriginalWebSocket = globalThis.WebSocket;
  const clipboardWriteText = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem('aegis_session_token', 'frontend-test-token');
    MockWebSocket.instances = [];
    Element.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { writeText: clipboardWriteText },
      configurable: true,
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    globalThis.WebSocket = OriginalWebSocket;
    clipboardWriteText.mockReset();
  });

  it('opens the global workflow drawer without replacing the composer draft', () => {
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /新建对话/i }));
    const composer = getComposer();
    setComposerText(composer, 'keep this draft');
    expect(screen.getByTestId('session-status-ticker')).toHaveTextContent('AWAITING FIRST TURN');

    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));

    expect(screen.getByRole('complementary', { name: /workflow for new investigation/i })).toBeInTheDocument();
    expect(within(screen.getByTestId('session-workflow')).getByText('AWAITING FIRST TURN')).toBeInTheDocument();
    expect(getComposer()).toHaveTextContent('keep this draft');
    expect(screen.getByTestId('chat-workspace')).toHaveStyle({ width: 'calc(50% - 5px)' });
    expect(screen.getByRole('tab', { name: /session workflow/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: /architecture test/i })).not.toBeInTheDocument();
    expect(screen.getByRole('separator', { name: /resize chat workspace panels/i })).toBeInTheDocument();
  });

  it('lists modified files after the final reply and refreshes an existing preview when reselected', async () => {
    const fileResponse = (content: string) => new Response(JSON.stringify({
      title: 'aegis/frontend/src/alpha.ts',
      type: 'typescript',
      content,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fileResponse('export const alpha = true;'))
      .mockResolvedValueOnce(fileResponse('export const alpha = "updated by a later turn";'));
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'Make several edits');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'modified-files', title: 'Edits', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    const sent = socket.sent.map((item) => JSON.parse(item)).find((item) => item.type === 'message.send');
    socket.emit({
      type: 'message.accepted',
      session_id: 'modified-files',
      turn_id: 'turn-edits',
      client_msg_id: sent.client_msg_id,
      source: 'main',
    });
    socket.emit({
      type: 'message.completed',
      session_id: 'modified-files',
      turn_id: 'turn-edits',
      message_id: 'assistant-edits',
      source: 'main',
      content: 'Edits complete.',
      modified_files: [
        'aegis/frontend/src/alpha.ts',
        'aegis/frontend/src/beta.ts',
        'aegis/backend/main.py',
        'README.md',
      ],
    });

    const modifiedFiles = await screen.findByTestId('modified-files');
    expect(modifiedFiles).toHaveAttribute('aria-label', 'Overview files');
    expect(within(modifiedFiles).getByText('OVERVIEW FILES')).toBeInTheDocument();
    expect(modifiedFiles.closest('[data-testid="chat-message"]')).toBeNull();
    const overviewToggle = within(modifiedFiles).getByRole('button', { name: /overview files/i });
    expect(overviewToggle).toHaveAttribute('aria-expanded', 'false');
    expect(within(modifiedFiles).queryByText('aegis/frontend/src/alpha.ts')).not.toBeInTheDocument();
    fireEvent.click(overviewToggle);
    expect(overviewToggle).toHaveAttribute('aria-expanded', 'true');
    expect(within(modifiedFiles).getByText('aegis/frontend/src/alpha.ts')).toBeInTheDocument();
    expect(within(modifiedFiles).getByText('README.md')).toBeInTheDocument();

    fireEvent.click(overviewToggle);
    expect(overviewToggle).toHaveAttribute('aria-expanded', 'false');
    expect(within(modifiedFiles).queryByText('aegis/frontend/src/alpha.ts')).not.toBeInTheDocument();
    fireEvent.click(overviewToggle);

    fireEvent.click(within(modifiedFiles).getByRole('button', { name: /aegis\/frontend\/src\/alpha\.ts/i }));
    const previewTab = await screen.findByRole('tab', { name: 'alpha.ts' });
    expect(previewTab).toHaveAttribute('aria-selected', 'true');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/drawer-html?path=aegis%2Ffrontend%2Fsrc%2Falpha.ts',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(await screen.findByText('export const alpha = true;')).toBeInTheDocument();
    fireEvent.click(within(modifiedFiles).getByRole('button', { name: /aegis\/frontend\/src\/alpha\.ts/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('export const alpha = "updated by a later turn";')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /refresh file preview alpha\.ts/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /close workflow visualization/i }));
    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    expect(screen.getByRole('tab', { name: 'alpha.ts' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: /close workflow visualization/i }));
    fireEvent.click(screen.getByRole('button', { name: /新建对话/i }));
    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    expect(screen.queryByRole('tab', { name: 'alpha.ts' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /close workflow visualization/i }));
    fireEvent.click(screen.getByText('Edits'));
    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    expect(screen.getByRole('tab', { name: 'alpha.ts' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /close file preview alpha\.ts/i }));
    expect(screen.queryByRole('tab', { name: 'alpha.ts' })).not.toBeInTheDocument();
  });

  it('automatically opens the last modified HTML file when the main task completes', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      title: 'latest.HTM',
      type: 'html',
      content: '<!doctype html><title>Latest preview</title>',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'Update HTML previews');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'html-auto-preview', title: 'HTML preview', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    socket.emit({
      type: 'message.completed',
      session_id: 'html-auto-preview',
      turn_id: 'turn-html-preview',
      message_id: 'assistant-html-preview',
      source: 'main',
      content: 'HTML files updated.',
      modified_files: ['reports/first.html', 'README.md', 'reports/latest.HTM'],
    });

    expect(await screen.findByRole('tab', { name: 'latest.HTM' })).toHaveAttribute('aria-selected', 'true');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/drawer-html?path=reports%2Flatest.HTM',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it('inserts messages from the active HTML preview at the composer caret and ignores hidden or foreign frames', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      title: 'interactive.html',
      type: 'html',
      content: '<!doctype html><title>Interactive preview</title>',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'Create interactive preview');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'interactive-preview', title: 'Interactive preview', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    socket.emit({
      type: 'message.completed',
      session_id: 'interactive-preview',
      turn_id: 'turn-interactive-preview',
      message_id: 'assistant-interactive-preview',
      source: 'main',
      content: 'Interactive preview is ready.',
      modified_files: ['reports/interactive.html'],
    });

    const frame = await screen.findByTestId('drawer-html-frame') as HTMLIFrameElement;
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-forms');
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
    const composer = getComposer();
    setComposerText(composer, 'before after');
    composer.focus();
    const textNode = composer.firstChild as Text;
    const range = document.createRange();
    range.setStart(textNode, 7);
    range.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    fireEvent.select(composer);
    fireEvent.keyUp(composer, { key: 'ArrowLeft' });
    socket.emit({
      type: 'approval.request',
      session_id: 'interactive-preview',
      approval_id: 'agent2ui-busy',
      command: 'review',
      description: 'Keep the composer draft while this approval is pending.',
    });
    await waitFor(() => expect(composer).toHaveAttribute('contenteditable', 'false'));
    const composerFocus = vi.spyOn(composer, 'focus');
    composerFocus.mockClear();
    frame.focus();

    fireEvent(window, new MessageEvent('message', {
      data: {
        channel: 'aegis-agent2ui',
        version: 1,
        type: 'composer.insert',
        text: 'middle ',
      },
      source: frame.contentWindow,
    }));
    expect(getComposer()).toHaveTextContent('before middle after');
    expect(composerFocus).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: /session workflow/i }));
    fireEvent(window, new MessageEvent('message', {
      data: {
        channel: 'aegis-agent2ui', version: 1, type: 'composer.insert', text: ' hidden',
      },
      source: frame.contentWindow,
    }));
    fireEvent(window, new MessageEvent('message', {
      data: {
        channel: 'aegis-agent2ui', version: 1, type: 'composer.insert', text: ' foreign',
      },
      source: window,
    }));
    expect(getComposer()).toHaveTextContent('before middle after');
  });

  it('does not auto-open HTML previews when the browser setting is disabled', async () => {
    window.localStorage.setItem('aegis_frontend_settings', JSON.stringify({
      chatAutoOpenHtmlOnTaskComplete: false,
    }));
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'Update one HTML file');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'html-disabled', title: 'Disabled preview', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    socket.emit({
      type: 'message.completed',
      session_id: 'html-disabled',
      turn_id: 'turn-html-disabled',
      message_id: 'assistant-html-disabled',
      source: 'main',
      content: 'HTML file updated.',
      modified_files: ['reports/disabled.html'],
    });

    expect(await screen.findByText('HTML file updated.')).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: /workflow/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('opens a queued HTML preview after returning to Chat', async () => {
    function ChatVisibilityHarness() {
      const [chatVisible, setChatVisible] = useState(true);
      return (
        <AegisChatProvider isChatVisible={chatVisible}>
          <button type="button" onClick={() => setChatVisible((visible) => !visible)}>
            Toggle Chat
          </button>
          {chatVisible ? <ChatTab agents={[]} /> : <p>Outside chat</p>}
        </AegisChatProvider>
      );
    }

    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      title: 'queued.html',
      type: 'html',
      content: '<!doctype html><title>Queued preview</title>',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatVisibilityHarness />);

    setComposerText(getComposer(), 'Update queued file');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'queued-html', title: 'Queued preview', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'Toggle Chat' }));
    expect(screen.getByText('Outside chat')).toBeInTheDocument();
    socket.emit({
      type: 'message.completed',
      session_id: 'queued-html',
      turn_id: 'turn-queued-html',
      message_id: 'assistant-queued-html',
      source: 'main',
      content: 'Queued HTML file updated.',
      modified_files: ['reports/queued.html'],
    });

    fireEvent.click(screen.getByRole('button', { name: 'Toggle Chat' }));
    expect(await screen.findByRole('tab', { name: 'queued.html' })).toHaveAttribute('aria-selected', 'true');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/drawer-html?path=reports%2Fqueued.html',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it('restores a user-scoped dynamic drawer tab and reloads its preview after remounting Chat', async () => {
    const userId = 'drawer-cache-user';
    window.localStorage.setItem('aegis_current_user', JSON.stringify({
      uid: userId,
      username: 'drawer-user',
      email: 'drawer-user@example.com',
      status: 'enabled',
      create_time: '2026-01-01T00:00:00Z',
      is_admin: false,
    }));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        title: 'cached.html', type: 'text', content: 'First load',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        title: 'cached.html', type: 'text', content: 'Reloaded content',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const firstRender = render(<ChatTab agents={[]} />);
    setComposerText(getComposer(), 'Create the cached preview.');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'drawer-cache-session', title: 'Cached drawer session', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    socket.emit({
      type: 'message.completed',
      session_id: 'drawer-cache-session',
      turn_id: 'drawer-cache-turn',
      message_id: 'drawer-cache-message',
      source: 'main',
      content: 'Cached file generated.',
      modified_files: ['reports/cached.html'],
    });
    expect(await screen.findByRole('tab', { name: 'cached.html' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('First load')).toBeInTheDocument();
    const cacheKey = getDrawerTabsStorageKey(userId)!;
    await waitFor(() => expect(window.localStorage.getItem(cacheKey)).toContain('reports/cached.html'));
    expect(window.localStorage.getItem(cacheKey)).not.toContain('First load');
    firstRender.unmount();

    render(<ChatTab agents={[]} />);
    expect(screen.queryByTestId('chat-workflow-drawer')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    expect(await screen.findByRole('tab', { name: 'cached.html' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Reloaded content')).toBeInTheDocument();
  });

  it('clears persisted drawer tabs when deleting a conversation or clearing local chat history', async () => {
    const userId = 'drawer-cleanup-user';
    window.localStorage.setItem('aegis_current_user', JSON.stringify({
      uid: userId,
      username: 'drawer-cleanup',
      email: 'drawer-cleanup@example.com',
      status: 'enabled',
      create_time: '2026-01-01T00:00:00Z',
      is_admin: false,
    }));
    window.localStorage.setItem('aegis_convs', JSON.stringify([{
      id: 'drawer-cleanup-session',
      title: 'Cleanup session',
      timestamp: '10:00',
      lastKnownRunState: 'idle',
      foregroundSource: 'main',
      foregroundAgentName: '',
      messages: [],
    }]));
    saveCachedDrawerTabs(userId, {
      'drawer-cleanup-session': {
        activeTab: 'workflow',
        tabs: [{ id: 'file:reports%2Fcleanup.html', path: 'reports/cleanup.html', title: 'cleanup.html' }],
      },
    }, ['drawer-cleanup-session']);
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /delete conversation cleanup session/i }));
    await waitFor(() => expect(window.localStorage.getItem(getDrawerTabsStorageKey(userId)!)).toBeNull());

    saveCachedDrawerTabs(userId, {
      'another-session': {
        activeTab: 'workflow',
        tabs: [{ id: 'file:reports%2Fanother.html', path: 'reports/another.html', title: 'another.html' }],
      },
    }, ['another-session']);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: /clear local chat cache/i }));
    expect(window.localStorage.getItem(getDrawerTabsStorageKey(userId)!)).toBeNull();
  });

  it('resizes the drawer by pointer and remembers the selected width', () => {
    const { unmount } = render(<ChatTab agents={[]} />);
    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));

    const drawer = screen.getByTestId('chat-workflow-drawer');
    const root = drawer.parentElement as HTMLDivElement;
    vi.spyOn(root, 'getBoundingClientRect').mockReturnValue({
      bottom: 800,
      height: 800,
      left: 0,
      right: 1000,
      top: 0,
      width: 1000,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const splitter = screen.getByRole('separator', { name: /resize chat workspace panels/i });
    fireEvent(splitter, new MouseEvent('pointerdown', { bubbles: true, clientX: 500 }));
    fireEvent(splitter, new MouseEvent('pointermove', { bubbles: true, clientX: 400 }));
    fireEvent(splitter, new MouseEvent('pointerup', { bubbles: true, clientX: 400 }));

    expect(drawer).toHaveStyle({ width: 'calc(40% - 5px)' });
    expect(window.localStorage.getItem('aegis_chat_workflow_drawer_width')).toBe('40');
    fireEvent.keyDown(splitter, { key: 'ArrowRight' });
    expect(drawer).toHaveStyle({ width: 'calc(41% - 5px)' });
    fireEvent.keyDown(splitter, { key: 'Home' });
    expect(drawer).toHaveStyle({ width: 'calc(36% - 5px)' });
    unmount();

    render(<ChatTab agents={[]} />);
    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    expect(screen.getByTestId('chat-workflow-drawer')).toHaveStyle({ width: 'calc(36% - 5px)' });
  });

  it('uses the status ticker as the accessible workflow entry point', () => {
    render(<ChatTab agents={[]} />);

    expect(screen.getByTestId('session-status-ticker')).toHaveTextContent('AWAITING FIRST TURN');
    expect(screen.queryByText('AEGIS PROCESSOR v0.2.3')).not.toBeInTheDocument();
    expect(screen.queryByText('INTENT ROUTING | A2A FLOW | VIP INTEGRATION PIPELINE')).not.toBeInTheDocument();

    const statusTicker = screen.getByTestId('session-status-ticker');
    fireEvent.keyDown(statusTicker, { key: 'Enter' });

    expect(screen.getByRole('complementary', { name: /workflow for current session/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /close workflow visualization/i }));
    fireEvent.keyDown(statusTicker, { key: ' ' });
    expect(screen.getByRole('complementary', { name: /workflow for current session/i })).toBeInTheDocument();
  });

  it('renders every ordinary chat message as GFM Markdown and can switch back to plain text', () => {
    window.localStorage.setItem('aegis_convs', JSON.stringify([{
      id: 'markdown-session',
      title: 'Markdown session',
      timestamp: '10:00',
      lastKnownRunState: 'idle',
      foregroundSource: 'main',
      foregroundAgentName: '',
      messages: [
        {
          id: 'markdown-user', sender: 'user', timestamp: '10:00', source: 'main',
          text: '## Operator brief\n\n- Verify the [reference](https://example.com)\n\n<img src="https://example.com/unsafe.png" alt="unsafe" />',
        },
        {
          id: 'markdown-main', sender: 'aegis', timestamp: '10:01', source: 'main',
          text: '**Confirmed**\n\n```bash\npwd\n```\n\n| IOC | Status |\n| --- | --- |\n| example.com | blocked |',
        },
        {
          id: 'markdown-delegate', sender: 'aegis', timestamp: '10:02', source: 'delegate', srcagent: 'threat-intel',
          text: '> Delegated finding',
        },
      ],
    }]));
    render(<ChatTab agents={[]} />);

    const [userCard, mainCard, delegateCard] = screen.getAllByTestId('chat-message');
    expect(within(userCard).getByRole('heading', { name: 'Operator brief', level: 2 })).toBeInTheDocument();
    const reference = within(userCard).getByRole('link', { name: 'reference' });
    expect(reference).toHaveAttribute('target', '_blank');
    expect(reference).toHaveAttribute('rel', 'noreferrer');
    expect(within(userCard).queryByRole('img', { name: 'unsafe' })).not.toBeInTheDocument();
    expect(within(mainCard).getByText('Confirmed').tagName).toBe('STRONG');
    const codeBlock = within(mainCard).getByText('pwd').closest('pre');
    expect(codeBlock).toHaveClass('aegis-markdown__pre');
    expect(within(mainCard).getByText('pwd')).toHaveClass('aegis-markdown__code');
    expect(within(mainCard).getByRole('table')).toBeInTheDocument();
    expect(within(delegateCard).getByText('Delegated finding').closest('blockquote')).toBeInTheDocument();
    expect(screen.getAllByTestId('message-text')).toHaveLength(3);
    screen.getAllByTestId('message-text').forEach((message) => {
      expect(message).toHaveAttribute('data-markdown-rendered', 'true');
    });

    fireEvent.click(screen.getByRole('button', { name: /disable markdown rendering/i }));
    expect(screen.queryByRole('heading', { name: 'Operator brief', level: 2 })).not.toBeInTheDocument();
    expect(within(userCard).getByTestId('message-text')).toHaveTextContent('## Operator brief');
    expect(within(mainCard).queryByRole('table')).not.toBeInTheDocument();
    screen.getAllByTestId('message-text').forEach((message) => {
      expect(message).toHaveAttribute('data-markdown-rendered', 'false');
      expect(message).toHaveClass('whitespace-pre-wrap');
    });

    fireEvent.click(screen.getByRole('button', { name: /enable markdown rendering/i }));
    expect(within(mainCard).getByRole('table')).toBeInTheDocument();
  });

  it('keeps prompt templates beside the Markdown control and exposes the A2UI preview option', () => {
    render(<ChatTab agents={[]} />);

    const promptTemplates = screen.getByRole('button', { name: /open prompt templates/i });
    const markdown = screen.getByRole('button', { name: /disable markdown rendering/i });
    expect(promptTemplates.parentElement).toBe(markdown.parentElement);

    const a2ui = screen.getByRole('button', { name: /enable a2ui preview/i });
    expect(a2ui).toHaveAttribute('aria-pressed', 'false');
    expect(a2ui).toHaveAttribute('title', 'A2UI preview');
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Adds the A2UI quick-instruction prefix when this message is sent.',
    );

    fireEvent.click(a2ui);
    expect(screen.getByRole('button', { name: /disable a2ui preview/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('prefixes A2UI submissions and supplies the current theme and date arguments', async () => {
    window.localStorage.setItem('aegis_theme', 'daylight-signal');
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'Create the incident overview.');
    fireEvent.click(screen.getByRole('button', { name: /enable a2ui preview/i }));
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({
      type: 'session.bound',
      session_id: 'a2ui-theme-session',
      title: 'A2UI theme',
      resumed: false,
    });
    await waitFor(() => {
      const payload = socket.sent
        .map((item) => JSON.parse(item))
        .find((item) => item.type === 'message.send');
      expect(payload).toMatchObject({
        text: '@[instruct_a2ui]\nCreate the incident overview.',
        args: { date: expect.any(String) },
      });
      expect(JSON.parse(payload.args.theme_color)).toEqual({
        style: 'Bright clarity for daytime investigation and report review.',
        background_surface: '#F4F7FB / #FFFFFF',
        accent: '#087EA4',
        text_muted: '#142235 / #52667B',
        border: '#C6D2E0',
      });
      expect(Number.isNaN(Date.parse(payload.args.date))).toBe(false);
    });
  });

  it('supplies the current theme and date arguments for standard submissions', async () => {
    window.localStorage.setItem('aegis_theme', 'daylight-signal');
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'Create the incident overview.');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({
      type: 'session.bound',
      session_id: 'standard-args-session',
      title: 'Standard arguments',
      resumed: false,
    });
    await waitFor(() => {
      const payload = socket.sent
        .map((item) => JSON.parse(item))
        .find((item) => item.type === 'message.send');
      expect(payload).toMatchObject({
        text: 'Create the incident overview.',
        args: { date: expect.any(String) },
      });
      expect(JSON.parse(payload.args.theme_color)).toEqual({
        style: 'Bright clarity for daytime investigation and report review.',
        background_surface: '#F4F7FB / #FFFFFF',
        accent: '#087EA4',
        text_muted: '#142235 / #52667B',
        border: '#C6D2E0',
      });
      expect(Number.isNaN(Date.parse(payload.args.date))).toBe(false);
    });
  });

  it('does not duplicate an A2UI instruction already present in the prompt', async () => {
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), '@[instruct_a2ui]\nCreate the incident overview.');
    fireEvent.click(screen.getByRole('button', { name: /enable a2ui preview/i }));
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({
      type: 'session.bound',
      session_id: 'a2ui-existing-instruction-session',
      title: 'A2UI existing instruction',
      resumed: false,
    });
    await waitFor(() => {
      const payload = socket.sent
        .map((item) => JSON.parse(item))
        .find((item) => item.type === 'message.send');
      expect(payload).toMatchObject({
        text: '@[instruct_a2ui]\nCreate the incident overview.',
        args: {
          theme_color: expect.any(String),
          date: expect.any(String),
        },
      });
    });
  });

  it('renders user shortcut instructions with the composer token treatment', () => {
    window.localStorage.setItem('aegis_convs', JSON.stringify([{
      id: 'shortcut-message-session',
      title: 'Shortcut message',
      timestamp: '10:00',
      lastKnownRunState: 'idle',
      foregroundSource: 'main',
      foregroundAgentName: '',
      messages: [{
        id: 'shortcut-message',
        sender: 'user',
        timestamp: '10:00',
        source: 'main',
        text: '@[instruct_a2ui]\nCreate the incident overview.',
      }],
    }]));
    render(<ChatTab agents={[]} />);

    const userCard = screen.getByTestId('chat-message');
    const token = within(userCard).getByText('@[instruct_a2ui]');
    expect(token.tagName).toBe('U');
    expect(token.parentElement).toHaveClass('aegis-shortcut-token');
    expect(token.parentElement?.tagName).toBe('STRONG');
    expect(within(userCard).getByTestId('message-text').textContent).toBe(
      '@[instruct_a2ui]\nCreate the incident overview.',
    );
  });

  it('centres the current status before starting the marquee', () => {
    vi.useFakeTimers();
    try {
      render(<ChatTab agents={[]} />);

      const ticker = screen.getByTestId('session-status-ticker');
      const viewport = ticker.querySelector('.aegis-session-status-ticker__viewport');
      expect(viewport).toHaveClass('aegis-session-status-ticker__viewport--announce');
      expect(within(ticker).getByText('AWAITING FIRST TURN')).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1500));
      expect(viewport).toHaveClass('aegis-session-status-ticker__viewport--static');
      expect(ticker.querySelector('.aegis-session-status-ticker__track')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('closes the global workflow drawer with its close control', () => {
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    fireEvent.click(screen.getByRole('button', { name: /close workflow visualization/i }));

    expect(screen.queryByRole('complementary', { name: /workflow for current session/i })).not.toBeInTheDocument();
    expect(screen.getByText('CENTRAL ARCHIVE')).toBeInTheDocument();
  });

  it('closes the global workflow drawer when Escape is pressed', () => {
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('complementary', { name: /workflow for current session/i })).not.toBeInTheDocument();
    expect(screen.getByText('CENTRAL ARCHIVE')).toBeInTheDocument();
  });

  it('groups prompt templates in a drawer and appends the selected prompt without sending it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      templates: [
        { id: 'tpl-1', tag: 'Triage', desc: 'Alert triage', prompt: 'Assess this alert.', create_time: '2026-01-01T00:00:00Z', update_time: '2026-01-02T00:00:00Z' },
        { id: 'tpl-2', tag: 'Triage', desc: 'Severity', prompt: 'Set a severity.', create_time: '2026-01-01T00:00:00Z', update_time: '2026-01-01T00:00:00Z' },
      ],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    render(<ChatTab agents={[]} />);

    const composer = getComposer();
    setComposerText(composer, 'Existing draft');
    fireEvent.click(screen.getByRole('button', { name: /open prompt templates/i }));

    expect(await screen.findByRole('complementary', { name: /prompt templates/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /triage templates/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /alert triage/i }));

    expect(screen.queryByRole('complementary', { name: /prompt templates/i })).not.toBeInTheDocument();
    expect(getComposer()).toHaveTextContent('Existing draft Assess this alert.');
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('navigates @ commands by keyboard, keeps typing after a token, and sends raw text', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/chat/quick-commands') {
        return new Response(JSON.stringify({
          commands: [
            {
              type: 'agent',
              name: 'incident-responder',
              desc: 'Investigates active incidents.',
              content: 'Use remote agent {agent_name} as {name}.',
            },
            {
              type: 'prompt',
              name: 'triage',
              desc: 'Classifies incident severity.',
              content: 'Classify the incident.',
            },
          ],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      throw new Error(`Unhandled request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /新建/i }));
    const composer = getComposer();
    fireEvent.focus(composer);
    setComposerText(composer, '@');

    const commandList = await screen.findByRole('listbox', { name: /available quick commands/i });
    const agentCommand = await within(commandList).findByRole('option', { name: /incident-responder/i });
    const promptCommand = await within(commandList).findByRole('option', { name: /triage/i });
    expect(agentCommand).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(composer, { key: 'ArrowDown' });
    expect(promptCommand).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(composer, { key: 'ArrowUp' });
    expect(agentCommand).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(composer, { key: 'Enter' });
    expect(composer.textContent).toBe('@[agent_incident-responder] ');
    const shortcutToken = document.querySelector('.aegis-shortcut-token');
    expect(shortcutToken).toHaveTextContent('@[agent_incident-responder]');
    expect(shortcutToken?.tagName).toBe('STRONG');
    expect(shortcutToken?.querySelector('u')).toHaveTextContent('@[agent_incident-responder]');
    setComposerText(composer, '@[agent_incident-responder] investigate now');
    expect(composer).toHaveTextContent('@[agent_incident-responder] investigate now');

    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'quick-command-session', title: 'Quick command', resumed: false });

    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    const payload = socket.sent.map((item) => JSON.parse(item)).find((item) => item.type === 'message.send');
    expect(payload).toMatchObject({ text: '@[agent_incident-responder] investigate now' });
  });

  it('restores the raw shortcut draft when the server rejects its template', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      commands: [{
        type: 'agent',
        name: 'unresolved-agent',
        desc: 'Uses an unsupported variable.',
        content: 'Delegate {region}.',
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /新建/i }));
    const composer = getComposer();
    setComposerText(composer, '@');
    const command = await screen.findByRole('option', { name: /unresolved-agent/i });
    fireEvent.mouseDown(command);
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'invalid-quick-command', title: 'Invalid quick command', resumed: false });
    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    const payload = socket.sent.map((item) => JSON.parse(item)).find((item) => item.type === 'message.send');
    socket.emit({
      type: 'error',
      code: 'invalid_quick_command',
      client_msg_id: payload.client_msg_id,
      message: 'Agent command has unsupported variable: {region}.',
    });

    expect(await screen.findByText(/unsupported variable/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(composer).toHaveTextContent('@[agent_unresolved-agent]');
    });
    expect(screen.queryAllByTestId('chat-message')).toHaveLength(0);
  });

  it('keeps transport errors with their session and clears them after the next accepted task', async () => {
    window.localStorage.setItem('aegis_convs', JSON.stringify([
      {
        id: 'session-a', sessionId: 'server-a', title: 'Session A', messages: [], timestamp: '10:00',
        lastKnownRunState: 'idle', foregroundSource: 'main', foregroundAgentName: '',
      },
      {
        id: 'session-b', sessionId: 'server-b', title: 'Session B', messages: [], timestamp: '10:01',
        lastKnownRunState: 'idle', foregroundSource: 'main', foregroundAgentName: '',
      },
    ]));
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'First task for A');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socketA = MockWebSocket.instances[0];
    socketA.emit({ type: 'session.bound', session_id: 'server-a', title: 'Session A', resumed: false });
    await waitFor(() => expect(socketA.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));

    fireEvent.click(screen.getByText('Session B'));
    setComposerText(getComposer(), 'Task for B');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));
    const socketB = MockWebSocket.instances[1];
    socketB.emit({ type: 'session.bound', session_id: 'server-b', title: 'Session B', resumed: false });
    await waitFor(() => expect(socketB.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));

    socketA.emit({ type: 'error', session_id: 'server-a', message: 'Session A failed.' });
    await waitFor(() => expect(screen.queryByText('Session A failed.')).not.toBeInTheDocument());

    fireEvent.click(screen.getByText('Session A'));
    expect(await screen.findByText('Session A failed.')).toBeInTheDocument();

    setComposerText(getComposer(), 'Retry task for A');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(socketA.sent.filter((item) => JSON.parse(item).type === 'message.send')).toHaveLength(2));
    const retry = socketA.sent
      .map((item) => JSON.parse(item))
      .filter((item) => item.type === 'message.send')[1];
    socketA.emit({
      type: 'message.accepted',
      session_id: 'server-a',
      turn_id: 'retry-turn-a',
      client_msg_id: retry.client_msg_id,
      source: 'main',
    });

    await waitFor(() => expect(screen.queryByText('Session A failed.')).not.toBeInTheDocument());
  });

  it('browses cached A2A agents in a dialog without sending a chat message', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        agents: [{
          name: 'aisoc', url: 'http://127.0.0.1:9086/a2a', status: 'active', available: true,
          description: 'SOC investigation agent', capabilities: ['Investigate security incidents'], error: null,
        }], global_routing: [], refreshed_at: '2026-01-01T00:00:00Z', stale: false, refresh_error: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        agents: [{
          name: 'aisoc', url: 'http://127.0.0.1:9086/a2a', status: 'active', available: true,
          description: 'SOC investigation agent', capabilities: ['Investigate security incidents', 'Respond to alerts'], error: null,
        }], global_routing: [], refreshed_at: '2026-01-01T00:01:00Z', stale: false, refresh_error: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /open a2a agents/i }));
    const dialog = await screen.findByRole('dialog', { name: /a2a agents/i });
    expect(within(dialog).getByRole('button', { name: /view agent aisoc/i })).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/a2a/context');

    fireEvent.click(within(dialog).getByRole('button', { name: /view agent aisoc/i }));
    expect(within(dialog).getByText('Investigate security incidents')).toBeInTheDocument();
    expect(within(dialog).getByText('http://127.0.0.1:9086/a2a')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /back to agents/i }));
    fireEvent.click(within(dialog).getByRole('button', { name: /refresh a2a agents/i }));
    await waitFor(() => expect(fetchMock.mock.calls[1][0]).toBe('/api/a2a/context/refresh'));
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' });
    expect(MockWebSocket.instances).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole('button', { name: /close a2a agents/i }));
    expect(screen.queryByRole('dialog', { name: /a2a agents/i })).not.toBeInTheDocument();
  });

  it('exits workspace fullscreen before Escape closes the workflow drawer', () => {
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    fireEvent.click(screen.getByRole('button', { name: /enter workspace fullscreen/i }));

    expect(screen.getByTestId('chat-workflow-drawer')).toHaveStyle({ width: '100%' });
    expect(screen.queryByTestId('chat-workspace')).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.getByTestId('chat-workflow-drawer')).toHaveStyle({ width: 'calc(50% - 5px)' });
    expect(screen.getByTestId('chat-workspace')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /enter workspace fullscreen/i })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('session-workflow')).not.toBeInTheDocument();
  });

  it('connects lazily and renders main state, delegate events, delegate tools, copy actions, and per-turn orchestration chains', async () => {
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /新建/i }));
    expect(MockWebSocket.instances).toHaveLength(0);

    expect(screen.getByText('CENTRAL ARCHIVE')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /collapse session sidebar/i }));
    expect(screen.queryByText('CENTRAL ARCHIVE')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /expand session sidebar/i }));
    expect(screen.getByText('CENTRAL ARCHIVE')).toBeInTheDocument();

    const initialComposer = getComposer();
    expect(initialComposer.tagName).toBe('DIV');
    fireEvent.click(screen.getByRole('button', { name: /expand composer/i }));
    expect(getComposer().tagName).toBe('DIV');

    setComposerText(getComposer(), 'hello over websocket');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1);
    });
    const socket = MockWebSocket.instances[0];

    await waitFor(() => {
      expect(socket.sent.length).toBeGreaterThan(0);
    });
    expect(JSON.parse(socket.sent[0])).toMatchObject({ type: 'session.bind' });
    expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(false);

    socket.emit({
      type: 'session.bound',
      session_id: 'sess-1',
      title: 'New Investigation',
      resumed: false,
    });

    await waitFor(() => {
      expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true);
    });

    const messageSend = socket.sent
      .map((item) => JSON.parse(item))
      .find((item) => item.type === 'message.send');
    expect(messageSend).toMatchObject({
      type: 'message.send',
      session_id: 'sess-1',
      text: 'hello over websocket',
    });

    socket.emit({
      type: 'run.state',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'main',
      state: 'running',
    });
    await waitFor(() => {
      const ticker = screen.getByTestId('session-status-ticker');
      expect(ticker).toHaveTextContent('AEGIS · MAIN EXECUTION IN PROGRESS');
      expect(within(ticker).getByText('AEGIS')).toHaveClass('aegis-session-status-ticker__part--actor');
      expect(within(ticker).getByText('MAIN EXECUTION IN PROGRESS')).toHaveClass(
        'aegis-session-status-ticker__part--activity',
      );
      expect(ticker.querySelector('.aegis-session-status-ticker__viewport')).toHaveClass(
        'aegis-session-status-ticker__viewport--announce',
      );
    });

    socket.emit({
      type: 'message.accepted',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      client_msg_id: messageSend.client_msg_id,
      source: 'main',
    });
    socket.emit({
      type: 'tool.started',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'main',
      tool_name: 'terminal',
      tool_call_id: 'tool-1',
      args_preview: 'pwd',
    });
    await waitFor(() => {
      expect(screen.getByTestId('session-status-ticker')).toHaveTextContent('AEGIS · TOOL CALL RUNNING · terminal');
    });
    socket.emit({
      type: 'tool.completed',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'main',
      tool_name: 'terminal',
      tool_call_id: 'tool-1',
      result_preview: '/Users/demo',
    });

    await waitFor(() => {
      const ticker = screen.getByTestId('session-status-ticker');
      expect(ticker).toHaveTextContent('AEGIS · TOOL CALL COMPLETED · terminal');
      expect(within(ticker).getByText('terminal')).toHaveClass('aegis-session-status-ticker__part--object');
    });

    expect(await screen.findByText(/orchestration chain/i)).toBeInTheDocument();
    expect(within(screen.getByTestId('message-chain')).getByText('terminal')).toBeInTheDocument();

    socket.emit({
      type: 'message.delta',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      message_id: 'assistant-1',
      source: 'main',
      delta: 'hello ',
    });
    socket.emit({
      type: 'message.completed',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      message_id: 'assistant-1',
      source: 'main',
      content: 'hello websocket world',
      completed: true,
    });

    expect(await screen.findByText('hello websocket world')).toBeInTheDocument();

    socket.emit({
      type: 'delegate.entered',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      child_session_id: 'delegate-sess',
    });
    socket.emit({
      type: 'run.state',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      state: 'waiting_for_delegate_input',
    });

    expect(await screen.findByText('threat-intel entered foreground')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('session-status-ticker')).toHaveTextContent('threat-intel · DELEGATE AWAITING INPUT');
    });

    socket.emit({
      type: 'tool.started',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      tool_name: 'terminal',
      tool_call_id: 'delegate-tool-1',
      args_preview: 'ls -la',
    });
    socket.emit({
      type: 'tool.started',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      tool_name: 'grep',
      tool_call_id: 'delegate-tool-2',
      args_preview: 'grep secret config.txt',
    });
    socket.emit({
      type: 'message.completed',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      message_id: 'delegate-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      content: 'delegate is waiting for your direction',
      completed: true,
    });

    expect(screen.queryByText('Delegate Tool Activity')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /toggle delegate tool messages/i }));
    expect(await screen.findByText('Delegate Tool Activity')).toBeInTheDocument();
    expect(screen.queryByText('ls -la')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /expand delegate tool details/i }));
    expect(await screen.findByText('ls -la')).toBeInTheDocument();
    expect(screen.getByText('grep secret config.txt')).toBeInTheDocument();

    socket.emit({
      type: 'tool.started',
      session_id: 'sess-1',
      turn_id: 'turn-2',
      source: 'delegate',
      srcagent: 'threat-intel',
      tool_name: 'cat',
      tool_call_id: 'delegate-tool-3',
      args_preview: 'cat evidence.txt',
    });

    await waitFor(() => {
      expect(screen.getAllByText('Delegate Tool Activity')).toHaveLength(2);
    });
    const secondDelegateToolCard = screen.getAllByText('Delegate Tool Activity')[1].closest('.relative');
    expect(secondDelegateToolCard).toBeTruthy();
    fireEvent.click(
      within(secondDelegateToolCard as HTMLElement).getByRole('button', { name: /expand delegate tool details/i }),
    );
    expect(await screen.findByText('cat evidence.txt')).toBeInTheDocument();

    socket.emit({
      type: 'delegate.exited',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      reason: 'return_to_main',
    });
    socket.emit({
      type: 'run.state',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'main',
      state: 'running',
    });
    socket.emit({
      type: 'message.completed',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      message_id: 'assistant-main-after-delegate',
      source: 'main',
      content: 'main agent resumed after /main',
      completed: true,
    });

    expect(await screen.findByText(/returned control to main/i)).toBeInTheDocument();
    expect(await screen.findByText('main agent resumed after /main')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('session-status-ticker')).toHaveTextContent(
        'threat-intel · DELEGATION COMPLETE · CONTROL RETURNED TO MAIN',
      );
    });

    socket.emit({
      type: 'run.state',
      session_id: 'sess-1',
      turn_id: 'turn-1',
      source: 'main',
      state: 'idle',
    });
    await waitFor(() => {
      const ticker = screen.getByTestId('session-status-ticker');
      expect(ticker).toHaveTextContent('SESSION COMPLETE · AWAITING NEXT REQUEST');
      expect(within(ticker).getByText('SESSION COMPLETE')).toHaveClass('aegis-session-status-ticker__part--complete');
      expect(within(ticker).getByText('AWAITING NEXT REQUEST')).toHaveClass('aegis-session-status-ticker__part--attention');
    });

    fireEvent.click(screen.getByRole('button', { name: /collapse composer/i }));
    expect(getComposer().tagName).toBe('DIV');

    const messageCards = screen.getAllByTestId('chat-message');
    const userMessage = messageCards.find((card) => card.getAttribute('data-sender') === 'user');
    const aegisMessage = messageCards.find((card) =>
      within(card).queryByText('hello websocket world'),
    );
    const delegateMessage = messageCards.find((card) =>
      within(card).queryByText('delegate is waiting for your direction'),
    );
    const chainMessage = messageCards.find((card) =>
      within(card).queryByText(/orchestration chain/i),
    );
    expect(userMessage).toBeTruthy();
    expect(aegisMessage).toBeTruthy();
    expect(delegateMessage).toBeTruthy();
    expect(chainMessage).toBeTruthy();
    expect(aegisMessage).toHaveClass('w-full');
    expect(within(aegisMessage as HTMLElement).getByTestId('message-text')).toHaveClass('select-text');
    expect(within(aegisMessage as HTMLElement).queryByText(/orchestration chain/i)).not.toBeInTheDocument();
    expect(within(chainMessage as HTMLElement).getByText(/orchestration chain/i)).toBeInTheDocument();
    expect(within(chainMessage as HTMLElement).getByText('terminal')).toBeInTheDocument();
    expect(within(chainMessage as HTMLElement).queryByText('threat-intel')).not.toBeInTheDocument();
    expect(within(aegisMessage as HTMLElement).getByText('AE')).toBeInTheDocument();
    expect(within(delegateMessage as HTMLElement).getByText('DG')).toBeInTheDocument();

    fireEvent.click(within(userMessage as HTMLElement).getByRole('button', { name: /copy message/i }));
    expect(clipboardWriteText).toHaveBeenLastCalledWith('hello over websocket');

    fireEvent.click(within(aegisMessage as HTMLElement).getByRole('button', { name: /copy message/i }));
    expect(clipboardWriteText).toHaveBeenLastCalledWith('hello websocket world');

    fireEvent.click(within(chainMessage as HTMLElement).getByRole('button', { name: /copy message/i }));
    expect(clipboardWriteText).toHaveBeenLastCalledWith('terminal\nCompleted\n/Users/demo');

    socket.emit({
      type: 'approval.request',
      session_id: 'sess-1',
      approval_id: 'approval-1',
      command: 'rm -rf /tmp/aegis-approval-test',
      description: 'recursive delete',
      choices: ['once', 'session', 'always', 'deny'],
      source: 'main',
    });

    expect(await screen.findByText(/approval required/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /allow once/i }));

    await waitFor(() => {
      expect(
        socket.sent.some((item) => {
          const payload = JSON.parse(item);
          return payload.type === 'approval.respond' && payload.choice === 'once';
        }),
      ).toBe(true);
    });

    const cached = JSON.parse(window.localStorage.getItem('aegis_convs') || '[]');
    expect(cached[0]).toMatchObject({
      sessionId: 'sess-1',
      workflowTraceVersion: 1,
      workflowTrace: expect.arrayContaining([
        expect.objectContaining({
          type: 'message.accepted',
          turnId: 'turn-1',
          source: 'main',
        }),
        expect.objectContaining({
          type: 'tool.completed',
          toolCallId: 'tool-1',
          resultPreview: '/Users/demo',
        }),
        expect.objectContaining({
          type: 'tool.started',
          turnId: 'turn-2',
          source: 'delegate',
          delegateId: expect.stringContaining('delegate:'),
        }),
      ]),
      messages: expect.arrayContaining([
        expect.objectContaining({
          text: 'hello over websocket',
          sender: 'user',
          turnId: 'turn-1',
          source: 'main',
        }),
        expect.objectContaining({ text: 'hello websocket world', sender: 'aegis' }),
        expect.objectContaining({ text: 'main agent resumed after /main', sender: 'aegis' }),
      ]),
    });

    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    expect(await screen.findByTestId('workflow-node-turn:turn-1')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:turn-1:tool-1')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-delegate:turn-1:delegate-sess')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-tool:turn-1:delegate-tool-1')).toBeInTheDocument();
    expect(screen.getByText('STATIC TRACE')).toBeInTheDocument();
  });

  it('keeps delegate delta text when its stream completes and renders final-only delegate replies', async () => {
    render(<ChatTab agents={[]} />);

    setComposerText(getComposer(), 'delegate please');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1);
    });
    const socket = MockWebSocket.instances[0];
    socket.emit({
      type: 'session.bound',
      session_id: 'sess-delegate-stream',
      title: 'Delegate stream',
      resumed: false,
    });

    await waitFor(() => {
      expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true);
    });

    socket.emit({
      type: 'message.delta',
      session_id: 'sess-delegate-stream',
      turn_id: 'turn-delegate-stream',
      message_id: 'delegate-stream-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      delta: 'streamed delegate result',
    });

    expect(await screen.findByText('streamed delegate result')).toBeInTheDocument();
    const messageCountBeforeCompletion = screen.getAllByTestId('chat-message').length;

    socket.emit({
      type: 'message.stream.completed',
      session_id: 'sess-delegate-stream',
      turn_id: 'turn-delegate-stream',
      message_id: 'delegate-stream-1',
      source: 'delegate',
      srcagent: 'threat-intel',
    });

    await waitFor(() => {
      expect(screen.getAllByText('streamed delegate result')).toHaveLength(1);
      expect(screen.getAllByTestId('chat-message')).toHaveLength(messageCountBeforeCompletion);
    });

    socket.emit({
      type: 'message.delta',
      session_id: 'sess-delegate-stream',
      turn_id: 'turn-delegate-stream',
      message_id: 'delegate-stream-2',
      source: 'delegate',
      srcagent: 'threat-intel',
      delta: 'next streamed delegate result',
    });

    await waitFor(() => {
      expect(screen.getByText('streamed delegate result')).toBeInTheDocument();
      expect(screen.getByText('next streamed delegate result')).toBeInTheDocument();
      expect(screen.getAllByTestId('chat-message')).toHaveLength(messageCountBeforeCompletion + 1);
    });

    socket.emit({
      type: 'message.completed',
      session_id: 'sess-delegate-stream',
      turn_id: 'turn-delegate-final-only',
      message_id: 'delegate-final-only-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      content: 'final without streamed delta',
      completed: true,
    });

    expect(await screen.findByText('final without streamed delta')).toBeInTheDocument();
  });

  it('persists and renders every direct A2A delegate turn as its own workflow sub-branch', async () => {
    render(<ChatTab agents={[]} />);

    const submit = (text: string) => {
      setComposerText(getComposer(), text);
      fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    };

    submit('Start a threat-intel delegate loop');
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({
      type: 'session.bound',
      session_id: 'sess-a2a-loop',
      title: 'A2A Loop',
      resumed: false,
    });
    await waitFor(() => {
      expect(socket.sent.filter((item) => JSON.parse(item).type === 'message.send')).toHaveLength(1);
    });
    const mainSend = socket.sent.map((item) => JSON.parse(item)).find((item) => item.type === 'message.send');
    socket.emit({
      type: 'message.accepted',
      server_event_id: 'sess-a2a-loop:1',
      ts: 1,
      session_id: 'sess-a2a-loop',
      turn_id: 'main-turn',
      client_msg_id: mainSend.client_msg_id,
      source: 'main',
    });
    socket.emit({
      type: 'tool.started',
      server_event_id: 'sess-a2a-loop:2',
      ts: 2,
      session_id: 'sess-a2a-loop',
      turn_id: 'main-turn',
      source: 'main',
      tool_name: 'aegis_a2a_delegate',
      tool_call_id: 'delegate-call',
      args_preview: '{"agent_name":"threat-intel"}',
    });
    socket.emit({
      type: 'delegate.entered',
      server_event_id: 'sess-a2a-loop:3',
      ts: 3,
      session_id: 'sess-a2a-loop',
      turn_id: 'main-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
    });
    socket.emit({
      type: 'message.delta',
      session_id: 'sess-a2a-loop',
      turn_id: 'main-turn',
      message_id: 'delegate-initial-reply',
      source: 'delegate',
      srcagent: 'threat-intel',
      delta: 'Initial delegated result',
    });
    socket.emit({
      type: 'message.stream.completed',
      server_event_id: 'sess-a2a-loop:4',
      ts: 4,
      session_id: 'sess-a2a-loop',
      turn_id: 'main-turn',
      message_id: 'delegate-initial-reply',
      source: 'delegate',
      srcagent: 'threat-intel',
    });
    socket.emit({
      type: 'run.state',
      server_event_id: 'sess-a2a-loop:5',
      ts: 5,
      session_id: 'sess-a2a-loop',
      turn_id: 'main-turn',
      source: 'delegate',
      srcagent: 'threat-intel',
      state: 'waiting_for_delegate_input',
    });

    submit('Inspect the first IOC set');
    await waitFor(() => {
      expect(socket.sent.filter((item) => JSON.parse(item).type === 'message.send')).toHaveLength(2);
    });
    const firstDelegateSend = socket.sent
      .map((item) => JSON.parse(item))
      .filter((item) => item.type === 'message.send')[1];
    socket.emit({
      type: 'message.accepted',
      server_event_id: 'sess-a2a-loop:6',
      ts: 6,
      session_id: 'sess-a2a-loop',
      turn_id: 'delegate-turn-1',
      client_msg_id: firstDelegateSend.client_msg_id,
      source: 'delegate',
      srcagent: 'threat-intel',
    });
    socket.emit({
      type: 'message.completed',
      server_event_id: 'sess-a2a-loop:7',
      ts: 7,
      session_id: 'sess-a2a-loop',
      turn_id: 'delegate-turn-1',
      message_id: 'delegate-reply-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      content: 'First IOC set completed',
      completed: true,
    });
    socket.emit({
      type: 'run.state',
      server_event_id: 'sess-a2a-loop:8',
      ts: 8,
      session_id: 'sess-a2a-loop',
      turn_id: 'delegate-turn-1',
      source: 'delegate',
      srcagent: 'threat-intel',
      state: 'waiting_for_delegate_input',
    });

    submit('Inspect the second IOC set');
    await waitFor(() => {
      expect(socket.sent.filter((item) => JSON.parse(item).type === 'message.send')).toHaveLength(3);
    });
    const secondDelegateSend = socket.sent
      .map((item) => JSON.parse(item))
      .filter((item) => item.type === 'message.send')[2];
    socket.emit({
      type: 'message.accepted',
      server_event_id: 'sess-a2a-loop:9',
      ts: 9,
      session_id: 'sess-a2a-loop',
      turn_id: 'delegate-turn-2',
      client_msg_id: secondDelegateSend.client_msg_id,
      source: 'delegate',
      srcagent: 'threat-intel',
    });
    socket.emit({
      type: 'message.completed',
      server_event_id: 'sess-a2a-loop:10',
      ts: 10,
      session_id: 'sess-a2a-loop',
      turn_id: 'delegate-turn-2',
      message_id: 'delegate-reply-2',
      source: 'delegate',
      srcagent: 'threat-intel',
      content: 'Second IOC set completed',
      completed: true,
    });
    socket.emit({
      type: 'run.state',
      server_event_id: 'sess-a2a-loop:11',
      ts: 11,
      session_id: 'sess-a2a-loop',
      turn_id: 'delegate-turn-2',
      source: 'delegate',
      srcagent: 'threat-intel',
      state: 'waiting_for_delegate_input',
    });

    fireEvent.click(screen.getByRole('button', { name: /open workflow visualization/i }));
    const delegateId = 'delegate:main-turn:delegate-call';
    expect(await screen.findByTestId(`workflow-node-${delegateId}`)).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-turn:delegate-turn-1')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-turn:delegate-turn-2')).toBeInTheDocument();
    expect(screen.getByTestId(`workflow-node-end:delegate:${delegateId}:main-turn`)).toBeInTheDocument();
    expect(screen.getByTestId(`workflow-node-end:delegate:${delegateId}:delegate-turn-1`)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`workflow-node-end:delegate:${delegateId}:delegate-turn-2`));
    expect(screen.getByText('FINAL MESSAGE')).toBeInTheDocument();
    expect(
      within(screen.getByTestId('workflow-node-details-scroll')).getByText('Second IOC set completed'),
    ).toBeInTheDocument();

    await waitFor(() => {
      const cached = JSON.parse(window.localStorage.getItem('aegis_convs') || '[]');
      const trace = cached[0]?.workflowTrace || [];
      expect(trace.filter((event: { type: string }) => event.type === 'message.accepted')).toHaveLength(3);
      expect(trace.filter((event: { type: string }) => event.type === 'message.completed')).toHaveLength(2);
      expect(trace.find((event: { id: string }) => event.id === 'sess-a2a-loop:9')).toMatchObject({
        delegateId,
        parentTurnId: 'main-turn',
      });
    });
  });

  it('sends clarify choices and routes Other into freeform clarify responses', async () => {
    render(<ChatTab agents={[]} />);

    fireEvent.click(screen.getAllByRole('button', { name: /新建/i })[0]);
    setComposerText(getComposer(), 'start clarify flow');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1);
    });
    const socket = MockWebSocket.instances[0];
    await waitFor(() => {
      expect(socket.sent.length).toBeGreaterThan(0);
    });

    socket.emit({
      type: 'session.bound',
      session_id: 'sess-clarify',
      title: 'Clarify Investigation',
      resumed: false,
    });

    await waitFor(() => {
      expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true);
    });

    socket.emit({
      type: 'clarify.request',
      session_id: 'sess-clarify',
      clarify_id: 'clarify-1',
      question: 'Which route should I take?',
      choices: ['alpha', 'beta'],
      source: 'main',
    });

    expect(await screen.findByText(/clarify required/i)).toBeInTheDocument();
    expect(screen.getByText('Which route should I take?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'alpha' }));

    await waitFor(() => {
      expect(
        socket.sent.some((item) => {
          const payload = JSON.parse(item);
          return payload.type === 'clarify.respond' && payload.answer === 'alpha';
        }),
      ).toBe(true);
    });

    socket.emit({
      type: 'clarify.request',
      session_id: 'sess-clarify',
      clarify_id: 'clarify-2',
      question: 'Add more details',
      choices: ['quick answer'],
      source: 'main',
    });

    expect(await screen.findByRole('button', { name: /other/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /other/i }));

    const composer = getComposer();
    setComposerText(composer, 'Need all Linux endpoints.');
    fireEvent.click(screen.getByRole('button', { name: /发送/i }));

    await waitFor(() => {
      expect(
        socket.sent.some((item) => {
          const payload = JSON.parse(item);
          return payload.type === 'clarify.respond' && payload.answer === 'Need all Linux endpoints.';
        }),
      ).toBe(true);
    });

    expect(
      socket.sent.some((item) => {
        const payload = JSON.parse(item);
        return payload.type === 'message.send' && payload.text === 'Need all Linux endpoints.';
      }),
    ).toBe(false);
  });

  it('uploads an image attachment and sends its validated metadata with an attachment-only turn', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      attachment: {
        id: 'att-image-1', kind: 'image', media_type: 'image/png',
        display_name: 'clipboard.png', size: 12,
      },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } })));
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:clipboard-image'),
      revokeObjectURL: vi.fn(),
    });
    render(<ChatTab agents={[]} />);

    const file = new File(['png-bytes'], 'clipboard.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText(/upload chat attachments/i), { target: { files: [file] } });
    expect(await screen.findByText('clipboard.png')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText(/uploading/i)).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /发送/i }));
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: 'session.bound', session_id: 'attachment-session', title: 'Attachment analysis', resumed: false });

    await waitFor(() => expect(socket.sent.some((item) => JSON.parse(item).type === 'message.send')).toBe(true));
    const payload = socket.sent.map((item) => JSON.parse(item)).find((item) => item.type === 'message.send');
    expect(payload).toMatchObject({ text: '', attachments: [{ id: 'att-image-1', kind: 'image' }] });
    expect(screen.getByTestId('message-attachments')).toHaveTextContent('clipboard.png');
  });

  it('uploads image files pasted into the composer without treating them as text', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      attachment: {
        id: 'att-paste-1', kind: 'image', media_type: 'image/png',
        display_name: 'pasted.png', size: 12,
      },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:pasted-image'),
      revokeObjectURL: vi.fn(),
    });
    render(<ChatTab agents={[]} />);

    const pasted = new File(['png-bytes'], 'pasted.png', { type: 'image/png' });
    fireEvent.paste(getComposer(), {
      clipboardData: { files: [pasted] },
    });

    expect(await screen.findByText('pasted.png')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/chat/attachments', expect.objectContaining({ method: 'POST' }));
  });
});
