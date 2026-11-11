// utils/useLiveRefresh.js: screens that show attendance reload when they
// come into view and every 30 s while in view, so a check-in shows up
// everywhere without restarting the app.
import React from 'react';
import { AppState, Text } from 'react-native';
import { render, act } from '@testing-library/react-native';

import useLiveRefresh, { LIVE_REFRESH_MS, formatUpdated } from '../useLiveRefresh';

// Focus = mounted, for these tests: the effect runs on mount and its
// cleanup on unmount, as React Navigation does on focus / blur.
jest.mock('@react-navigation/native', () => {
  const R = require('react');
  return { useFocusEffect: (cb) => R.useEffect(cb, [cb]) };
});

let appStateListener = null;
const removeListener = jest.fn();

beforeEach(() => {
  jest.useFakeTimers();
  appStateListener = null;
  removeListener.mockClear();
  AppState.currentState = 'active';
  jest.spyOn(AppState, 'addEventListener').mockImplementation((_, fn) => {
    appStateListener = fn;
    return { remove: removeListener };
  });
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function Probe({ load, deps = [] }) {
  const { lastUpdated } = useLiveRefresh(load, deps);
  return <Text testID="stamp">{lastUpdated ? formatUpdated(lastUpdated) : 'never'}</Text>;
}

const flush = () => act(async () => {});

test('loads on opening, then every 30 seconds while open', async () => {
  const load = jest.fn().mockResolvedValue(true);
  const screen = await render(<Probe load={load} />);
  await flush();
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId('stamp').props.children).toMatch(/^Updated /);

  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS); });
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS); });
  expect(load).toHaveBeenCalledTimes(3);
});

test('stops when the screen closes', async () => {
  const load = jest.fn().mockResolvedValue(true);
  const screen = await render(<Probe load={load} />);
  await flush();
  await screen.unmount();
  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS * 3); });
  expect(load).toHaveBeenCalledTimes(1);
  expect(removeListener).toHaveBeenCalled();
});

test('skips the timer while the app is in the background and reloads on return', async () => {
  const load = jest.fn().mockResolvedValue(true);
  await render(<Probe load={load} />);
  await flush();

  AppState.currentState = 'background';
  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS * 2); });
  expect(load).toHaveBeenCalledTimes(1);

  AppState.currentState = 'active';
  await act(async () => { appStateListener('active'); });
  expect(load).toHaveBeenCalledTimes(2);
});

test('a failed load does not claim the data is up to date', async () => {
  const load = jest.fn().mockResolvedValue(false);
  const screen = await render(<Probe load={load} />);
  await flush();
  expect(screen.getByTestId('stamp').props.children).toBe('never');

  load.mockRejectedValueOnce(new Error('offline'));
  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS); });
  expect(screen.getByTestId('stamp').props.children).toBe('never');
});

test('never runs two loads at once', async () => {
  let finish;
  const load = jest.fn(() => new Promise((resolve) => { finish = resolve; }));
  await render(<Probe load={load} />);
  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS * 2); });
  expect(load).toHaveBeenCalledTimes(1);
  await act(async () => { finish(true); });
  await act(async () => { jest.advanceTimersByTime(LIVE_REFRESH_MS); });
  expect(load).toHaveBeenCalledTimes(2);
});

test('reloads at once when the chosen month changes, using the newest load', async () => {
  const first = jest.fn().mockResolvedValue(true);
  const second = jest.fn().mockResolvedValue(true);
  const screen = await render(<Probe load={first} deps={[10]} />);
  await flush();
  await screen.rerender(<Probe load={second} deps={[11]} />);
  await flush();
  expect(first).toHaveBeenCalledTimes(1);
  expect(second).toHaveBeenCalledTimes(1);
});
