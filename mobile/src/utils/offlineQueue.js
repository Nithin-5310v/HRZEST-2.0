import AsyncStorage from '@react-native-async-storage/async-storage';

const QUEUE_KEY = 'offline_punch_queue';
// The server refuses punches older than this anyway; dropping them keeps a
// shared phone from holding someone's location history indefinitely.
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

// Whose punches the queue serves right now: AuthContext's owner key
// ("employee:<company>:<id>"), set on sign-in/restore, null when signed out.
// Each punch remembers its owner, so a punch made offline is sent only with
// that person's own login -- never under another account that signs in on
// the same phone -- and survives sign-out until they're back online and
// signed in again (signing out used to delete it silently).
let currentOwner = null;
export const setQueueOwner = (ownerKey) => {
  currentOwner = ownerKey || null;
};

const readAll = async () => {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch (_) {
    return [];
  }
};
const writeAll = (list) => AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(list));

// Punches saved before owners were recorded belong to whoever is signed in
// (the old code wiped them whenever the account changed).
const isMine = (p) => (p.owner ?? currentOwner) === currentOwner;

// `mocked` records whether the location fix came from a mock-location provider,
// so a punch refused online for that reason can't be laundered through the
// offline queue and accepted on sync.
export async function queuePunch(lat = null, lon = null, { mocked = false } = {}) {
  const punch = {
    id: `${Date.now().toString()}-${Math.random().toString(36).slice(2)}`,
    punched_at: new Date().toISOString(),
    lat,
    lon,
    mocked: !!mocked,
    owner: currentOwner,
  };
  const queue = await readAll();
  queue.push(punch);
  await writeAll(queue);
  return punch;
}

// The signed-in person's pending punches, oldest first.
export async function getPendingPunches() {
  if (!currentOwner) return [];
  return (await readAll()).filter(isMine);
}

// Removes just these punches (the ones a sync sent) -- a punch queued while
// the sync was in flight stays for the next one.
export async function removePunches(ids) {
  const drop = new Set(ids);
  await writeAll((await readAll()).filter((p) => !drop.has(p.id)));
}

export async function removeFromQueue(id) {
  await removePunches([id]);
}

// Clears the signed-in person's punches only.
export async function clearQueue() {
  await writeAll((await readAll()).filter((p) => !isMine(p)));
}

// On an account change: drops punches too old to sync and any saved before
// owners were recorded (their owner is unknown, so nobody may send them).
export async function pruneQueue(now = Date.now()) {
  await writeAll((await readAll()).filter(
    (p) => p.owner && now - new Date(p.punched_at).getTime() < MAX_AGE_MS,
  ));
}

// Sends the signed-in person's pending punches. One sync at a time.
// Resolves to { sent, synced, failed } or null when there was nothing to
// do or the server couldn't be reached (punches stay queued).
let syncing = null;
export function syncPendingPunches(send) {
  if (syncing) return syncing;
  syncing = (async () => {
    const punches = await getPendingPunches();
    if (punches.length === 0) return null;
    try {
      const res = await send(punches.map(({ owner, ...p }) => p));
      if (!res?.data?.ok) return null;
      await removePunches(punches.map((p) => p.id));
      const results = res.data.results || [];
      const failed = results.filter((r) => !r.ok).length;
      return { sent: punches.length, synced: punches.length - failed, failed };
    } catch (_) {
      return null; // still offline: keep them
    }
  })().finally(() => { syncing = null; });
  return syncing;
}
