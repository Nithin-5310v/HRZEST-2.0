import React, { createContext, useContext, useState, useEffect } from 'react';
import * as LocalAuthentication from 'expo-local-authentication';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  adminLogout, employeeLogout, fetchMyEmployeeSession, setCompanySlug, setUnauthorizedHandler,
} from '../api/client';
import { secureGetItem, secureSetItem, secureMultiRemove } from '../utils/secureStorage';
import { getBiometricLockEnabled, setLastCompanySlug } from '../utils/preferences';
import { clearLocalEmployees } from '../utils/employeeStore';
import { clearQueue as clearOfflinePunchQueue } from '../utils/offlineQueue';
import { registerForPushNotifications } from '../utils/pushNotifications';

const AuthContext = createContext(null);

// 'hr_return': the HR session (token + user) kept aside while an HR account
// uses its own employee view (switchToEmployeeView), to switch back to.
const SESSION_KEYS = ['token', 'user', 'user_role', 'user_id', 'hr_return'];

// Plain (non-secure) AsyncStorage key -- not a credential, just a marker of
// "which account last used this device," so a signOut()/kill followed by a
// DIFFERENT account signing in can be detected and the previous account's
// locally-cached data (locally-created employees, queued offline punches --
// both plain AsyncStorage, neither scoped to an account or company) wiped
// before the new session ever reads it. Deliberately persists across
// signOut() and app kills (only ever overwritten by a successful signIn())
// so it still catches the "app was killed, never called signOut(), a
// different account logs in next launch" case, not just a clean logout.
const DEVICE_SESSION_OWNER_KEY = 'device_session_owner';

// Both current identity fields this app's login responses actually
// provide: admin sessions carry `name` (the admin username itself -- see
// LoginScreen.js's handleAdminLogin), employee sessions carry
// `employeeId`. Prefixed with role so an admin and an employee that
// happen to share a string never collide, and with the company
// (`companySlug`, '' for the server's default one) since "admin" or
// "EMP001" at one company is a different account from the same name at
// another.
const ownerKeyFor = (userData) =>
  `${userData?.role || 'unknown'}:${userData?.companySlug || ''}:` +
  `${userData?.role === 'employee' ? userData?.employeeId : userData?.name}`;

// Wipes every locally-persisted, non-session-keyed cache this app keeps --
// currently the admin-side "created employees" cache and the employee-side
// offline attendance punch queue. Called whenever the account about to use
// the device differs from whichever account last did (see
// DEVICE_SESSION_OWNER_KEY above), and unconditionally on every clean
// signOut(), so neither survives into a different account's session on the
// same device.
const clearCrossAccountCaches = async () => {
  await Promise.all([
    clearLocalEmployees().catch(() => {}),
    clearOfflinePunchQueue().catch(() => {}),
  ]);
};

export function AuthProvider({ children }) {
  const [user, setUser]     = useState(null);   // { role:'admin'|'employee', adminRole:'admin'|'hr'|'manager' (only set when role==='admin'), name, employeeId? }
  const [loading, setLoading] = useState(true);
  // App-lock: a session restored from disk on cold start is gated behind a
  // biometric prompt (when the device has one enrolled) before its data is
  // shown, same as banking/enterprise apps. A fresh signIn() never locks --
  // the user just proved identity with a password.
  const [locked, setLocked] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const token = await secureGetItem('token');
        const saved = await secureGetItem('user');
        if (token && saved) {
          const restoredUser = JSON.parse(saved);
          // Before anything renders: every API call from here on goes to this company.
          setCompanySlug(restoredUser.companySlug);
          setUser(restoredUser);
          try {
            const wantsLock = await getBiometricLockEnabled();
            const hasHw = wantsLock && await LocalAuthentication.hasHardwareAsync();
            const isEnrolled = hasHw && await LocalAuthentication.isEnrolledAsync();
            if (isEnrolled) setLocked(true);
          } catch (_) {}
        } else {
          await secureMultiRemove(SESSION_KEYS);
          setUser(null);
        }
      } catch (_) {}
      setLoading(false);
    })();

    // Any 401 from the API (expired/revoked token) clears the session and
    // drops the user back on the login screen instead of leaving them
    // stuck retrying calls with a dead token.
    setUnauthorizedHandler(() => {
      secureMultiRemove(SESSION_KEYS).catch(() => {});
      setCompanySlug('');
      setUser(null);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = async (token, userData) => {
    // Cross-account cache isolation: if the account signing in now isn't
    // the same one that last used this device (whether the last session
    // ended via a clean signOut() or the app was simply killed), wipe the
    // previous account's locally-cached data first -- see
    // clearCrossAccountCaches()/DEVICE_SESSION_OWNER_KEY above for why
    // this can't just be handled at signOut() time alone.
    const newOwnerKey = ownerKeyFor(userData);
    try {
      const previousOwnerKey = await AsyncStorage.getItem(DEVICE_SESSION_OWNER_KEY);
      if (previousOwnerKey && previousOwnerKey !== newOwnerKey) {
        await clearCrossAccountCaches();
      }
      await AsyncStorage.setItem(DEVICE_SESSION_OWNER_KEY, newOwnerKey);
    } catch (_) {}

    // userData.companySlug: the company this session belongs to ('' or
    // missing = the server's default company).
    setCompanySlug(userData?.companySlug);
    setLastCompanySlug(userData?.companySlug).catch(() => {});
    await secureSetItem('token', token);
    await secureSetItem('user', JSON.stringify(userData));
    setUser(userData);
    setLocked(false);
    // Fire-and-forget -- registerForPushNotifications() is fully
    // self-contained (permissions, token fetch, upload) and must never
    // block or fail a login; it already no-ops safely wherever push isn't
    // set up yet (no EAS project, permission denied, etc), but the .catch
    // here is a second line of defense against an unhandled rejection
    // (e.g. a mocked/monkeypatched caller in tests) since this call is
    // never awaited.
    registerForPushNotifications(userData).catch(() => {});
  };

  const signOut = async () => {
    try {
      if (user?.role === 'admin') {
        await adminLogout();
      } else if (user?.role === 'employee') {
        await employeeLogout();
      }
      // Signing out of the employee view signs the kept HR session out too.
      const hrReturn = JSON.parse((await secureGetItem('hr_return')) || 'null');
      if (hrReturn?.token) {
        await secureSetItem('token', hrReturn.token);
        await adminLogout();
      }
    } catch (_) {}
    // Only now: the logout call above still had to reach this company.
    setCompanySlug('');
    await secureMultiRemove(SESSION_KEYS);
    await clearCrossAccountCaches();
    setUser(null);
    setLocked(false);
  };

  // HR, or the owner admin with their own profile -> their own employee view
  // (the website's "My Profile" switch). Not signIn(): it's the same person,
  // so the account-change cache wipe (offline punches, cached employees)
  // must not run.
  const switchToEmployeeView = async () => {
    const { data } = await fetchMyEmployeeSession();
    const employeeUser = {
      role: 'employee',
      name: data.name,
      employeeId: data.employee_id,
      email: data.email,
      companySlug: user?.companySlug,
      company: user?.company,
      hrReturn: true,
      // Which panel "Back to ..." returns to: 'hr' or 'admin'.
      returnPanel: user?.adminRole === 'hr' ? 'hr' : 'admin',
    };
    const hrToken = await secureGetItem('token');
    await secureSetItem('hr_return', JSON.stringify({ token: hrToken, user }));
    await secureSetItem('token', data.token);
    await secureSetItem('user', JSON.stringify(employeeUser));
    setUser(employeeUser);
  };

  // Back to the HR panel with the kept HR session; ends the employee token.
  const switchBackToHr = async () => {
    const hrReturn = JSON.parse((await secureGetItem('hr_return')) || 'null');
    if (!hrReturn?.token) return signOut();
    try {
      await employeeLogout();
    } catch (_) {}
    await secureSetItem('token', hrReturn.token);
    await secureSetItem('user', JSON.stringify(hrReturn.user));
    await secureMultiRemove(['hr_return']);
    setUser(hrReturn.user);
  };

  const unlockApp = async () => {
    try {
      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: 'Unlock HRzest',
        cancelLabel: 'Cancel',
        disableDeviceFallback: false, // allow device PIN/pattern as a fallback
      });
      if (result.success) {
        setLocked(false);
        return true;
      }
    } catch (_) {}
    return false;
  };

  const updateUser = async (partialData) => {
    setUser((prev) => {
      if (!prev) return partialData;
      const updated = { ...prev, ...partialData };
      secureSetItem('user', JSON.stringify(updated)).catch(() => {});
      return updated;
    });
  };

  return (
    <AuthContext.Provider
      value={{ user, loading, locked, signIn, signOut, updateUser, unlockApp, switchToEmployeeView, switchBackToHr }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
