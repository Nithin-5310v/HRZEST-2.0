import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { renderHook, act } from '@testing-library/react-native';

// Split into its own file (rather than a second describe() block appended
// to AuthContext.test.js) after that combination proved flaky under Jest
// + @testing-library/react-native: a renderHook() call in a SECOND
// describe() block in the same file intermittently got back a null
// useContext() value (and RTL warned of "overlapping act() calls"),
// reproducible even with an exact duplicate of an already-passing test --
// i.e. unrelated to anything test-specific here, purely an artifact of
// two describe() blocks' renderHook() calls sharing one file's module/
// React-tree lifecycle. Jest gives every test FILE its own isolated
// module registry, so a second file sidesteps the interaction entirely
// rather than continuing to fight it.
jest.mock('../../api/client', () => ({
  adminLogout: jest.fn().mockResolvedValue({}),
  employeeLogout: jest.fn().mockResolvedValue({}),
  setUnauthorizedHandler: jest.fn(),
  setCompanySlug: jest.fn(),
}));

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

// signIn() fires this off after every login -- mocked at this boundary
// (like api/client above) rather than mocking expo-notifications/
// expo-constants directly, since registerForPushNotifications() itself
// already has its own dedicated tests (utils/__tests__/pushNotifications.test.js).
jest.mock('../../utils/pushNotifications', () => ({
  registerForPushNotifications: jest.fn().mockResolvedValue(undefined),
}));

import { AuthProvider, useAuth } from '../AuthContext';
import { registerForPushNotifications } from '../../utils/pushNotifications';
import { __store as secureStoreBackingStore } from 'expo-secure-store';

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
