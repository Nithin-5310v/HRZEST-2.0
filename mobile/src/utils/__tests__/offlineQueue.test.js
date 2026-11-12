import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  queuePunch, getPendingPunches, clearQueue, removeFromQueue, removePunches,
  setQueueOwner, pruneQueue, syncPendingPunches,
} from '../offlineQueue';

const ME = 'employee::E1';
const OTHER = 'employee::E2';

describe('offlineQueue', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    setQueueOwner(ME);
  });

  it('starts empty', async () => {
    expect(await getPendingPunches()).toEqual([]);
  });

  it('queues a punch with an id, timestamp and its owner', async () => {
    const punch = await queuePunch(12.34, 56.78);
    expect(punch.id).toBeTruthy();
    expect(punch.punched_at).toBeTruthy();
    expect(punch.lat).toBe(12.34);
    expect(punch.lon).toBe(56.78);
    expect(punch.owner).toBe(ME);
    expect(await getPendingPunches()).toEqual([punch]);
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
    expect((await getPendingPunches()).map((p) => p.id)).toEqual([first.id, second.id]);
  });

  it('removeFromQueue removes only the matching punch', async () => {
    const first = await queuePunch();
    const second = await queuePunch();
    await removeFromQueue(first.id);
    const pending = await getPendingPunches();
    expect(pending).toHaveLength(1);
    expect(pending[0].id).toBe(second.id);
  });

  describe('owners', () => {
    it('shows each person only their own punches', async () => {
      await queuePunch(1, 1);
      setQueueOwner(OTHER);
      expect(await getPendingPunches()).toEqual([]);
      await queuePunch(2, 2);
      expect(await getPendingPunches()).toHaveLength(1);
      setQueueOwner(ME);
      expect((await getPendingPunches())[0].lat).toBe(1);
    });

    it('has nothing to send while signed out, and keeps the punches', async () => {
      await queuePunch();
      setQueueOwner(null);
      expect(await getPendingPunches()).toEqual([]);
      setQueueOwner(ME);
      expect(await getPendingPunches()).toHaveLength(1);
    });

    it("clearQueue clears only the signed-in person's punches", async () => {
      await queuePunch();
      setQueueOwner(OTHER);
      await queuePunch();
      await clearQueue();
      expect(await getPendingPunches()).toEqual([]);
      setQueueOwner(ME);
      expect(await getPendingPunches()).toHaveLength(1);
    });

    it('pruneQueue drops punches with no owner and ones too old to sync', async () => {
      const old = new Date(Date.now() - 8 * 24 * 3600 * 1000).toISOString();
      await AsyncStorage.setItem('offline_punch_queue', JSON.stringify([
        { id: 'legacy', punched_at: new Date().toISOString() },
        { id: 'old', punched_at: old, owner: ME },
        { id: 'fresh', punched_at: new Date().toISOString(), owner: ME },
      ]));
      await pruneQueue();
      expect((await getPendingPunches()).map((p) => p.id)).toEqual(['fresh']);
    });
  });

  describe('syncPendingPunches', () => {
    it('sends the punches without their owner field and removes them', async () => {
      await queuePunch(1, 2);
      const send = jest.fn().mockResolvedValue({ data: { ok: true, results: [{ ok: true }] } });
      const result = await syncPendingPunches(send);
      expect(send.mock.calls[0][0][0].owner).toBeUndefined();
      expect(result).toEqual({ sent: 1, synced: 1, failed: 0 });
      expect(await getPendingPunches()).toEqual([]);
    });

    it('keeps a punch queued while the sync was in flight', async () => {
      await queuePunch(1, 1);
      let finish;
      const send = jest.fn(() => new Promise((resolve) => { finish = resolve; }));
      const syncing = syncPendingPunches(send);
      await new Promise((r) => setTimeout(r, 0));
      const late = await queuePunch(2, 2);
      finish({ data: { ok: true, results: [{ ok: true }] } });
      await syncing;
      expect((await getPendingPunches()).map((p) => p.id)).toEqual([late.id]);
    });

    it('runs one sync at a time', async () => {
      await queuePunch();
      const send = jest.fn().mockResolvedValue({ data: { ok: true, results: [] } });
      await Promise.all([syncPendingPunches(send), syncPendingPunches(send)]);
      expect(send).toHaveBeenCalledTimes(1);
    });

    it('keeps the punches when the server cannot be reached', async () => {
      await queuePunch();
      const result = await syncPendingPunches(jest.fn().mockRejectedValue(new Error('offline')));
      expect(result).toBeNull();
      expect(await getPendingPunches()).toHaveLength(1);
    });

    it('counts punches the server rejected', async () => {
      await queuePunch();
      await queuePunch();
      const send = jest.fn().mockResolvedValue({ data: { ok: true, results: [{ ok: true }, { ok: false }] } });
      expect(await syncPendingPunches(send)).toEqual({ sent: 2, synced: 1, failed: 1 });
    });

    it('removePunches removes only the given ids', async () => {
      const a = await queuePunch();
      const b = await queuePunch();
      await removePunches([a.id]);
      expect((await getPendingPunches()).map((p) => p.id)).toEqual([b.id]);
    });
  });
});
