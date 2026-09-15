import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { renderHook, act } from '@testing-library/react-native';

jest.mock('../../api/client', () => ({
  adminLogout: jest.fn().mockResolvedValue({}),
  employeeLogout: jest.fn().mockResolvedValue({}),
  setUnauthorizedHandler: jest.fn(),
}));

// Stateful, not just resolved-value stubs -- employeeStore.js now persists
// its locally-created-employee cache through secureStorage.js (native ->
// expo-secure-store), so these tests' saveLocalEmployee()/getLocalEmployees()
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
}));

// signIn() now fires this off after every login -- mocked at this boundary
// (like api/client above) rather than mocking expo-notifications/
// expo-constants directly, since registerForPushNotifications() itself
// already has its own dedicated tests (pushNotifications.test.js).
jest.mock('../../utils/pushNotifications', () => ({
  registerForPushNotifications: jest.fn().mockResolvedValue(undefined),
}));

import { AuthProvider, useAuth } from '../AuthContext';
import { registerForPushNotifications } from '../../utils/pushNotifications';
import { saveLocalEmployee, getLocalEmployees } from '../../utils/employeeStore';
import { queuePunch, getPendingPunches } from '../../utils/offlineQueue';
import { __store as secureStoreBackingStore } from 'expo-secure-store';

// AuthContext.signIn()/signOut() are responsible for keeping two plain
// (non-account-scoped) AsyncStorage caches -- employeeStore's locally-
// created-employee list and offlineQueue's pending punch queue -- from
// bleeding across accounts on a shared device. See the "Cross-tenant data
// bleed on device reuse" fix: neither cache carries a company/account id
// of its own, so isolation has to be enforced at the sign-in/sign-out
// boundary instead.
describe('AuthContext cross-account cache isolation', () => {
  const wrapper = ({ children }) => <AuthProvider>{children}</AuthProvider>;

  beforeEach(async () => {
    await AsyncStorage.clear();
    secureStoreBackingStore.clear();
  });

  it('keeps cached data when the same account signs in again', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    await act(async () => {
      await saveLocalEmployee({ employee_id: 'X1', name: 'Cached' });
      await queuePunch(1, 2);
    });

    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });

    expect(await getLocalEmployees()).toHaveLength(1);
    expect(await getPendingPunches()).toHaveLength(1);
  });

  it('wipes cached data when a different account signs in', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });
    await act(async () => {
      await saveLocalEmployee({ employee_id: 'X1', name: 'Cached' });
      await queuePunch(1, 2);
    });

    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E2', name: 'Bob' });
    });

    expect(await getLocalEmployees()).toHaveLength(0);
    expect(await getPendingPunches()).toHaveLength(0);
  });

  it('wipes cached data across admin vs employee role, even with an overlapping identifier', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'admin', name: 'E1' });
    });
    await act(async () => {
      await saveLocalEmployee({ employee_id: 'X1', name: 'Cached' });
    });

    await act(async () => {
      await result.current.signIn('tok2', { role: 'employee', employeeId: 'E1', name: 'Alice' });
    });

    expect(await getLocalEmployees()).toHaveLength(0);
  });

  it('wipes cached data on a clean signOut()', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'admin', name: 'admin1' });
    });
    await act(async () => {
      await saveLocalEmployee({ employee_id: 'X1', name: 'Cached' });
    });

    await act(async () => {
      await result.current.signOut();
    });

    expect(await getLocalEmployees()).toHaveLength(0);
  });

  it('detects a mismatch after an unclean kill (no signOut) followed by a different account', async () => {
    // First "session": sign in, cache data, then the component tree is
    // torn down without ever calling signOut() -- simulating the app
    // being killed outright rather than the user logging out.
    const first = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await first.result.current.signIn('tok1', { role: 'admin', name: 'admin1' });
    });
    await act(async () => {
      await saveLocalEmployee({ employee_id: 'X1', name: 'Cached' });
    });
    first.unmount();

    // Fresh mount (simulating an app relaunch) -- a different account logs in.
    const second = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await second.result.current.signIn('tok2', { role: 'admin', name: 'admin2' });
    });

    expect(await getLocalEmployees()).toHaveLength(0);
    // Unlike `first` above, this tree was never explicitly unmounted --
    // left dangling, it bled into whichever describe block ran next
    // (surfaced by adding the push-notification tests below: their
    // renderHook() calls intermittently got back a null context, and RTL
    // warned of "overlapping act() calls").
    second.unmount();
  });
});

describe('AuthContext push notification registration', () => {
  const wrapper = ({ children }) => <AuthProvider>{children}</AuthProvider>;

  beforeEach(async () => {
    await AsyncStorage.clear();
    secureStoreBackingStore.clear();
    registerForPushNotifications.mockClear();
  });

  it('registers for push notifications with the signed-in user after signIn()', async () => {
    const { result } = await renderHook(() => useAuth(), { wrapper });
    const userData = { role: 'employee', employeeId: 'E1', name: 'Alice' };
    await act(async () => {
      await result.current.signIn('tok1', userData);
    });
    expect(registerForPushNotifications).toHaveBeenCalledWith(userData);
  });

  it('does not block signIn() if push registration itself rejects', async () => {
    registerForPushNotifications.mockRejectedValueOnce(new Error('boom'));
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('tok1', { role: 'admin', name: 'admin1' });
    });
    expect(result.current.user).toEqual(expect.objectContaining({ name: 'admin1' }));
  });
});
