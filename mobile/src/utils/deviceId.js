import { secureGetItem, secureSetItem } from './secureStorage';

// A random, per-install identifier sent with attendance punches so the server
// can notice one device punching for many employees (utils/detections.py).
// It is correlation data, NOT a credential: it grants nothing and a user can
// reset it by reinstalling. Real device binding needs platform attestation.
const KEY = 'hrz_device_id';
let cached = null;

const randomHex = (n) => {
  let out = '';
  for (let i = 0; i < n; i += 1) out += Math.floor(Math.random() * 16).toString(16);
  return out;
};

export async function getDeviceId() {
  if (cached) return cached;
  try {
    let id = await secureGetItem(KEY);
    if (!id) {
      id = `d-${randomHex(24)}`;
      await secureSetItem(KEY, id);
    }
    cached = id;
    return id;
  } catch (_) {
    return null; // storage unavailable: omit rather than block a punch
  }
}
