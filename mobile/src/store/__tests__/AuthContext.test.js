import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { renderHook, act } from '@testing-library/react-native';

jest.mock('../../api/client', () => ({
  adminLogout: jest.fn().mockResolvedValue({}),
  employeeLogout: jest.fn().mockResolvedValue({}),
  setUnauthorizedHandler: jest.fn(),
  setCompanySlug: jest.fn(),
}));

// Stateful, not just resolved-value stubs -- employeeStore.js now persists
// its locally-created-employee cache through secureStorage.js (native ->
// expo-secure-store), so these tests' secure-storage reads and writes
// round trips need a fake that actually remembers what was written, not one
// that always resolves the same static value regardless of prior calls.
jest.mock('expo-secure-store', () => {
  const store = new Map();
  return {
    __store: store,
    getItemAsync: jest.fn((key) => Promise.resolve(store.has(key) ? store.get(key) : null)),
    setItemAsync: jest.fn((key, value) => {
      store.set(key, value);
      return Promise.resolve();
    }),
    deleteItemAsync: jest.fn((key) => {
      store.delete(key);
      return Promise.resolve();
    }),
  };
});

jest.mock('expo-local-authentication', () => ({
  hasHardwareAsync: jest.fn().mockResolvedValue(false),
  isEnrolledAsync: jest.fn().mockResolvedValue(false),
  authenticateAsync: jest.fn().mockResolvedValue({ success: true }),
}));

jest.mock('../../utils/preferences', () => ({
  getBiometricLockEnabled: jest.fn().mockResolvedValue(false),
  setLastCompanySlug: jest.fn().mockResolvedValue(undefined),
}));

// signIn() now fires this off after every login -- mocked at this boundary
// (like api/client above) rather than mocking expo-notifications/
// expo-constants directly, since registerForPushNotifications() itself
// already has its own dedicated tests, in this file's sibling
// AuthContext.pushNotifications.test.js (split out to its own file after
// a second describe() block's renderHook() calls proved flaky sharing
// this file with the tests below -- see that file's header comment).
jest.mock('../../utils/pushNotifications', () => ({
  registerForPushNotifications: jest.fn().mockResolvedValue(undefined),
}));

import { AuthProvider, useAuth } from '../AuthContext';
import { queuePunch, getPendingPunches } from '../../utils/offlineQueue';
import { __store as secureStoreBackingStore } from 'expo-secure-store';

const OLD_STAFF_CACHE = '@custom_created_employees_v1';
const allPunches = async () => JSON.parse((await AsyncStorage.getItem('offline_punch_queue')) || '[]');

// Offline punches are tagged with the account that made them
// (utils/offlineQueue.js): AuthContext tells the queue whose session it is,
// so a punch is only ever sent with its owner's own login. Signing out or
// another account signing in on the same phone no longer deletes it.
// The staff-list copy older versions kept is removed whenever the account
// changes or someone signs out.
describe('AuthContext offline punches and old caches', () => {
  const wrapper = ({ children }) => <AuthProvider>{children}</AuthProvider>;

  beforeEach(async () => {
    await AsyncStorage.clear();
    secureStoreBackingStore.clear();
  });

  it('keeps punches when the same account signs in again', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    await act(async () => {
      await queuePunch(1, 2);
    });
    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    expect(await getPendingPunches()).toHaveLength(1);
  });

  it("never offers one person's punches to a different account", async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    await act(async () => {
      await queuePunch(1, 2);
    });
    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E2', name: 'Bob' });
    });
    expect(await getPendingPunches()).toHaveLength(0);
    // Alice's punch is still on the phone for when she signs back in.
    expect(await allPunches()).toHaveLength(1);
    await act(async () => {
      await result.current.signIn('tok3', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    expect(await getPendingPunches()).toHaveLength(1);
  });

  it('separates the same id across admin vs employee and across companies', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'employee', employeeId: 'E1', companySlug: 'acme' });
    });
    await act(async () => {
      await queuePunch(1, 2);
    });
    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E1', companySlug: 'globex' });
    });
    expect(await getPendingPunches()).toHaveLength(0);
    await act(async () => {
      await result.current.signIn('tok3', { role: 'admin', name: 'E1', companySlug: 'acme' });
    });
    expect(await getPendingPunches()).toHaveLength(0);
  });

  it('keeps unsent punches through sign-out (they used to be deleted)', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    await act(async () => {
      await queuePunch(1, 2);
    });
    await act(async () => {
      await result.current.signOut();
    });
    expect(await getPendingPunches()).toHaveLength(0); // nobody signed in
    expect(await allPunches()).toHaveLength(1);
    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    expect(await getPendingPunches()).toHaveLength(1);
  });

  it('drops punches with no recorded owner when the account changes', async () => {
    await AsyncStorage.setItem('offline_punch_queue', JSON.stringify([
      { id: 'legacy', punched_at: new Date().toISOString(), lat: 1, lon: 2 },
    ]));
    await AsyncStorage.setItem('device_session_owner', 'employee::E1');
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E2', name: 'Bob' });
    });
    expect(await allPunches()).toHaveLength(0);
  });

  it("removes the old staff-list copy on sign-out and when the account changes", async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'admin', name: 'admin1' });
    });
    secureStoreBackingStore.set(OLD_STAFF_CACHE, '[{"employee_id":"X1"}]');
    await act(async () => {
      await result.current.signOut();
    });
    expect(secureStoreBackingStore.has(OLD_STAFF_CACHE)).toBe(false);

    secureStoreBackingStore.set(OLD_STAFF_CACHE, '[{"employee_id":"X1"}]');
    await act(async () => {
      await result.current.signIn('tok2', { role: 'admin', name: 'admin2' });
    });
    expect(secureStoreBackingStore.has(OLD_STAFF_CACHE)).toBe(false);
  });

  it('sends the API to the signed-in company, and back to the default one on signOut()', async () => {
    const { setCompanySlug } = require('../../api/client');
    const { setLastCompanySlug } = require('../../utils/preferences');
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'admin', name: 'admin', companySlug: 'acme' });
    });
    expect(setCompanySlug).toHaveBeenLastCalledWith('acme');
    expect(setLastCompanySlug).toHaveBeenLastCalledWith('acme');

    await act(async () => {
      await result.current.signOut();
    });
    expect(setCompanySlug).toHaveBeenLastCalledWith('');
  });

});
