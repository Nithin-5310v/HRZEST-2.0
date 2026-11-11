// HR staff switching between the HR panel and their own employee view,
// like the website's "My Profile" button: the HR session is kept aside and
// restored without signing in again; the switch is not an account change,
// so the offline punch queue survives it.
import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { renderHook, act } from '@testing-library/react-native';

jest.mock('../../api/client', () => ({
  adminLogout: jest.fn().mockResolvedValue({}),
  employeeLogout: jest.fn().mockResolvedValue({}),
  fetchMyEmployeeSession: jest.fn(),
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

jest.mock('../../utils/pushNotifications', () => ({
  registerForPushNotifications: jest.fn().mockResolvedValue(undefined),
}));

import * as api from '../../api/client';
import { AuthProvider, useAuth } from '../AuthContext';
import { queuePunch, getPendingPunches } from '../../utils/offlineQueue';
import { __store as secureStore } from 'expo-secure-store';

const HR_USER = { role: 'admin', adminRole: 'hr', name: 'EMP7', companySlug: 'acme', company: 'Acme' };

describe('AuthContext HR <-> employee view', () => {
  const wrapper = ({ children }) => <AuthProvider>{children}</AuthProvider>;

  beforeEach(async () => {
    jest.clearAllMocks();
    await AsyncStorage.clear();
    secureStore.clear();
    api.fetchMyEmployeeSession.mockResolvedValue({
      data: { ok: true, token: 'emp-token', employee_id: 'EMP7', name: 'Priya', email: 'priya@acme.in' },
    });
  });

  const signedInAsHr = async () => {
    const hook = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await hook.result.current.signIn('hr-token', HR_USER);
    });
    return hook;
  };

  it('switches to the employee view and back without signing in again', async () => {
    const { result } = await signedInAsHr();

    await act(async () => {
      await result.current.switchToEmployeeView();
    });
    expect(result.current.user).toMatchObject({
      role: 'employee', employeeId: 'EMP7', name: 'Priya', companySlug: 'acme', hrReturn: true,
    });
    expect(secureStore.get('token')).toBe('emp-token');

    await act(async () => {
      await result.current.switchBackToHr();
    });
    expect(result.current.user).toEqual(HR_USER);
    expect(secureStore.get('token')).toBe('hr-token');
    expect(secureStore.has('hr_return')).toBe(false);
    expect(api.employeeLogout).toHaveBeenCalledTimes(1);
  });

  it('keeps queued offline punches across the switch (same person)', async () => {
    const { result } = await signedInAsHr();
    await act(async () => {
      await result.current.switchToEmployeeView();
    });
    await queuePunch(12.97, 77.59);
    await act(async () => {
      await result.current.switchBackToHr();
    });
    expect(await getPendingPunches()).toHaveLength(1);
  });

  it('signing out of the employee view ends the kept HR session too', async () => {
    const { result } = await signedInAsHr();
    await act(async () => {
      await result.current.switchToEmployeeView();
    });
    await act(async () => {
      await result.current.signOut();
    });
    expect(api.employeeLogout).toHaveBeenCalled();
    expect(api.adminLogout).toHaveBeenCalled();
    expect(result.current.user).toBeNull();
    expect(secureStore.has('hr_return')).toBe(false);
    expect(secureStore.has('token')).toBe(false);
  });

  it('remembers which panel to go back to: HR for HR', async () => {
    const { result } = await signedInAsHr();
    await act(async () => {
      await result.current.switchToEmployeeView();
    });
    expect(result.current.user.returnPanel).toBe('hr');
  });

  it('takes the owner admin to their own profile and back to the admin panel', async () => {
    const OWNER = { role: 'admin', adminRole: 'admin', name: 'owner@acme.in', companySlug: 'acme',
      company: 'Acme', linkedEmployeeId: 'EMP9' };
    api.fetchMyEmployeeSession.mockResolvedValue({
      data: { ok: true, token: 'own-emp-token', employee_id: 'EMP9', name: 'Ravi', email: 'owner@acme.in' },
    });
    const { result } = await renderHook(() => useAuth(), { wrapper });
    await act(async () => {
      await result.current.signIn('owner-token', OWNER);
    });
    await act(async () => {
      await result.current.switchToEmployeeView();
    });
    expect(result.current.user).toMatchObject({
      role: 'employee', employeeId: 'EMP9', hrReturn: true, returnPanel: 'admin',
    });
    expect(secureStore.get('token')).toBe('own-emp-token');
    await act(async () => {
      await result.current.switchBackToHr();
    });
    expect(result.current.user).toEqual(OWNER);
    expect(secureStore.get('token')).toBe('owner-token');
  });

  it('stays in the HR panel when the switch is refused', async () => {
    api.fetchMyEmployeeSession.mockRejectedValue({ response: { data: { msg: 'No personal employee profile' } } });
    const { result } = await signedInAsHr();
    await act(async () => {
      await expect(result.current.switchToEmployeeView()).rejects.toBeTruthy();
    });
    expect(result.current.user).toEqual(HR_USER);
    expect(secureStore.get('token')).toBe('hr-token');
  });
});
