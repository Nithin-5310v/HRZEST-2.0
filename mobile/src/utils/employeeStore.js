// Employee PII (name, email, phone, role, department) used to sit in plain
// AsyncStorage -- unencrypted on both platforms, readable off a rooted/
// jailbroken device or extracted from a plain device backup. Routed through
// secureStorage.js instead: Keychain/Keystore-backed on native builds (the
// same wrapper AuthContext.js already uses for the session token/user
// object), AsyncStorage only as its documented web fallback (no Keychain/
// Keystore in a browser).
import { secureRemoveItem } from './secureStorage';

const STORAGE_KEY = '@custom_created_employees_v1';

// The app no longer keeps its own copy of the staff list (it kept
// employees deleted on the website, and duplicated renumbered ones).
// clearLocalEmployees() wipes what older versions saved.
export const clearLocalEmployees = async () => {
  try {
    await secureRemoveItem(STORAGE_KEY);
  } catch (e) {
    // best-effort -- nothing else to fall back to
  }
};
