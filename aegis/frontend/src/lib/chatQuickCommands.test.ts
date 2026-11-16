import { describe, expect, it } from 'vitest';

import {
  findShortcutQuery,
  quickCommandToken,
  type ChatQuickCommand,
} from './chatQuickCommands';

const commands: ChatQuickCommand[] = [
  {
    type: 'agent',
    name: 'incident-responder',
    desc: 'Investigates incidents.',
    content: 'Delegate to {agent_name} as {name}.',
  },
  {
    type: 'prompt',
    name: 'Triage',
    desc: 'Classify alerts.',
    content: 'Classify the alert.',
  },
  {
    type: 'instruct',
    name: 'Evidence',
    desc: 'Preserve evidence.',
    content: 'Do not modify originals.',
  },
];

describe('chat quick commands', () => {
  it('finds the active @ query at the composer caret', () => {
    expect(findShortcutQuery('Investigate @inci', 17)).toEqual({
      query: 'inci',
      start: 12,
      end: 17,
    });
    const selectedToken = 'Investigate @[agent_incident-responder]';
    expect(findShortcutQuery(selectedToken, selectedToken.length)).toBeNull();
  });

  it('renders the canonical shortcut token', () => {
    expect(quickCommandToken(commands[0])).toBe('@[agent_incident-responder]');
  });

  it('keeps command content opaque to the composer', () => {
    expect(commands.map(quickCommandToken)).toEqual([
      '@[agent_incident-responder]',
      '@[prompt_Triage]',
      '@[instruct_Evidence]',
    ]);
  });
});
