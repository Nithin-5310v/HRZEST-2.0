import AsyncStorage from '@react-native-async-storage/async-storage';
import { queuePunch, getPendingPunches, clearQueue, removeFromQueue } from '../offlineQueue';

describe('offlineQueue', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
  });

  it('starts empty', async () => {
    expect(await getPendingPunches()).toEqual([]);
  });

  it('queues a punch and returns it with a generated id and timestamp', async () => {
    const punch = await queuePunch(12.34, 56.78);
    expect(punch.id).toBeTruthy();
    expect(punch.punched_at).toBeTruthy();
    expect(punch.lat).toBe(12.34);
    expect(punch.lon).toBe(56.78);

    const pending = await getPendingPunches();
    expect(pending).toHaveLength(1);
    expect(pending[0]).toEqual(punch);
  });

  it('queues without coordinates when none are given', async () => {
    const punch = await queuePunch();
    expect(punch.lat).toBeNull();
    expect(punch.lon).toBeNull();
  });

  it('records whether the location fix was mocked', async () => {
    const real = await queuePunch(1, 2);
    const fake = await queuePunch(1, 2, { mocked: true });
    expect(real.mocked).toBe(false);
    expect(fake.mocked).toBe(true);
  });

  it('appends multiple punches in order', async () => {
    const first = await queuePunch();
    const second = await queuePunch();
    const pending = await getPendingPunches();
    expect(pending.map((p) => p.id)).toEqual([first.id, second.id]);
  });

  it('removeFromQueue removes only the matching punch', async () => {
    const first = await queuePunch();
    const second = await queuePunch();
    await removeFromQueue(first.id);
    const pending = await getPendingPunches();
    expect(pending).toHaveLength(1);
    expect(pending[0].id).toBe(second.id);
  });

  it('clearQueue empties the queue', async () => {
    await queuePunch();
    await queuePunch();
    await clearQueue();
    expect(await getPendingPunches()).toEqual([]);
  });
});
