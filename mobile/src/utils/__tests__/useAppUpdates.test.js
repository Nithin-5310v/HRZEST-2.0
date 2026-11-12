jest.mock('expo-updates', () => ({
  __esModule: true,
  isEnabled: true,
  checkForUpdateAsync: jest.fn(),
  fetchUpdateAsync: jest.fn(),
  reloadAsync: jest.fn(() => Promise.resolve()),
}));

import * as Updates from 'expo-updates';
import { checkForAppUpdate } from '../useAppUpdates';

describe('checkForAppUpdate', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    Updates.isEnabled = true;
  });

  it('offers to restart once a new update has downloaded', async () => {
    Updates.checkForUpdateAsync.mockResolvedValue({ isAvailable: true });
    Updates.fetchUpdateAsync.mockResolvedValue({ isNew: true });
    const ask = jest.fn();
    expect(await checkForAppUpdate({ ask, dev: false })).toBe('ready');
    const buttons = ask.mock.calls[0][2];
    expect(buttons.map((b) => b.text)).toEqual(['Later', 'Restart now']);
    buttons[1].onPress();
    expect(Updates.reloadAsync).toHaveBeenCalled();
  });

  it('says nothing when there is no update', async () => {
    Updates.checkForUpdateAsync.mockResolvedValue({ isAvailable: false });
    const ask = jest.fn();
    expect(await checkForAppUpdate({ ask, dev: false })).toBe('none');
    expect(ask).not.toHaveBeenCalled();
  });

  it('does nothing when updates are off', async () => {
    Updates.isEnabled = false;
    expect(await checkForAppUpdate({ ask: jest.fn(), dev: false })).toBe('disabled');
    expect(Updates.checkForUpdateAsync).not.toHaveBeenCalled();
  });

  it('does nothing in development', async () => {
    expect(await checkForAppUpdate({ ask: jest.fn(), dev: true })).toBe('disabled');
    expect(Updates.checkForUpdateAsync).not.toHaveBeenCalled();
  });
});
