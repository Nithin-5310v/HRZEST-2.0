import AsyncStorage from '@react-native-async-storage/async-storage';

// Plain, non-sensitive user preferences (booleans/flags only) — never put
// session/credential data here, use secureStorage.js for that.
const BIOMETRIC_LOCK_KEY = '@pref_biometric_lock_enabled';
const NOTIFICATIONS_KEY = '@pref_notifications_enabled';
const DARK_MODE_KEY = '@pref_dark_mode_enabled';

export const getBiometricLockEnabled = async () => {
  const v = await AsyncStorage.getItem(BIOMETRIC_LOCK_KEY);
  return v === null ? true : v === 'true'; // opt-out, not opt-in
};

export const setBiometricLockEnabled = (enabled) =>
  AsyncStorage.setItem(BIOMETRIC_LOCK_KEY, enabled ? 'true' : 'false');

export const getNotificationsEnabled = async () => {
  const v = await AsyncStorage.getItem(NOTIFICATIONS_KEY);
  return v === null ? true : v === 'true';
};

export const setNotificationsEnabled = (enabled) =>
  AsyncStorage.setItem(NOTIFICATIONS_KEY, enabled ? 'true' : 'false');

export const getDarkModeEnabled = async () => {
  const v = await AsyncStorage.getItem(DARK_MODE_KEY);
  return v === 'true'; // opt-in, defaults to light
};

export const setDarkModeEnabled = (enabled) =>
  AsyncStorage.setItem(DARK_MODE_KEY, enabled ? 'true' : 'false');

// The company URL slug last signed in with, so the login screen can fill
// it in again -- it's in every URL the company's team uses, not a secret.
const LAST_COMPANY_KEY = '@pref_last_company_slug';

export const getLastCompanySlug = async () => (await AsyncStorage.getItem(LAST_COMPANY_KEY)) || '';

export const setLastCompanySlug = (slug) => AsyncStorage.setItem(LAST_COMPANY_KEY, slug || '');
