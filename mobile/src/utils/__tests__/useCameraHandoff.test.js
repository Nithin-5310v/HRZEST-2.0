// useCameraHandoff: the attendance scanners' camera previews mount only
// after the previous camera is gone, and a camera that never starts is
// restarted once, then reported -- instead of a silent black preview.
import { renderHook, act } from '@testing-library/react-native';

import useCameraHandoff, { CAMERA_HANDOFF_DELAY_MS } from '../useCameraHandoff';

const START_TIMEOUT_MS = 5000;

const renderCamera = (initialProps) =>
  renderHook(({ active, step, facing }) => useCameraHandoff(active, step, facing), { initialProps });

const tick = (ms) => act(async () => { jest.advanceTimersByTime(ms); });

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

it('mounts the preview only after the handoff delay', async () => {
  const { result } = await renderCamera({ active: true, step: 'qr', facing: 'back' });
  expect(result.current.ready).toBe(false);
  await tick(CAMERA_HANDOFF_DELAY_MS);
  expect(result.current.ready).toBe(true);
});

it('drops the preview on the very render that changes step, so the old camera goes first', async () => {
  const { result, rerender } = await renderCamera({ active: true, step: 'qr', facing: 'back' });
  await tick(CAMERA_HANDOFF_DELAY_MS);
  await act(async () => result.current.onCameraReady());
  expect(result.current.open).toBe(true);

  await rerender({ active: true, step: 'face', facing: 'front' });
  expect(result.current.ready).toBe(false);
  await tick(CAMERA_HANDOFF_DELAY_MS);
  expect(result.current.ready).toBe(true);
  expect(result.current.open).toBe(false);
});

it('restarts a camera that never opens once, then reports it', async () => {
  const { result } = await renderCamera({ active: true, step: 'face', facing: 'front' });
  await tick(CAMERA_HANDOFF_DELAY_MS);
  const firstKey = result.current.cameraKey;

  await tick(START_TIMEOUT_MS);
  expect(result.current.cameraKey).not.toBe(firstKey);
  expect(result.current.error).toBeNull();

  // Each timer is scheduled by an effect after the previous one's render.
  await tick(CAMERA_HANDOFF_DELAY_MS);
  await tick(START_TIMEOUT_MS);
  expect(result.current.error).toMatch(/didn't start/);

  await act(async () => result.current.retry());
  expect(result.current.error).toBeNull();
  expect(result.current.ready).toBe(false);
});

it('leaves a camera that opened alone', async () => {
  const { result } = await renderCamera({ active: true, step: 'face', facing: 'front' });
  await tick(CAMERA_HANDOFF_DELAY_MS);
  const key = result.current.cameraKey;
  await act(async () => result.current.onCameraReady());
  await tick(START_TIMEOUT_MS * 3);
  expect(result.current.cameraKey).toBe(key);
  expect(result.current.error).toBeNull();
});

it('reports a mount error straight away', async () => {
  const { result } = await renderCamera({ active: true, step: 'face', facing: 'front' });
  await tick(CAMERA_HANDOFF_DELAY_MS);
  await act(async () => result.current.onMountError({ message: 'Camera could not be started' }));
  expect(result.current.error).toBe('Camera could not be started');
});

it("waits for a fresh handoff and a fresh ready after being released (back from the phone's camera)", async () => {
  const { result, rerender } = await renderCamera({ active: true, step: 'face', facing: 'front' });
  await tick(CAMERA_HANDOFF_DELAY_MS);
  await act(async () => result.current.onCameraReady());
  expect(result.current.open).toBe(true);

  await rerender({ active: false, step: 'face', facing: 'front' });
  expect(result.current.ready).toBe(false);
  await rerender({ active: true, step: 'face', facing: 'front' });
  expect(result.current.ready).toBe(false);
  await tick(CAMERA_HANDOFF_DELAY_MS);
  expect(result.current.ready).toBe(true);
  expect(result.current.open).toBe(false);
});

it('does nothing while inactive (no permission yet, or the phone camera in use)', async () => {
  const { result } = await renderCamera({ active: false, step: 'qr', facing: 'back' });
  await tick(CAMERA_HANDOFF_DELAY_MS + START_TIMEOUT_MS * 3);
  expect(result.current.ready).toBe(false);
  expect(result.current.error).toBeNull();
});
