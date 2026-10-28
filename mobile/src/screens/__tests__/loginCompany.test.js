// The login screen's Company URL picks which company the sign-in goes to:
// it's applied (api/client.js's setCompanySlug) before the login call, and
// the session remembers it (signIn's companySlug).
import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import LoginScreen from '../LoginScreen';
import * as api from '../../api/client';
import { PORTAL_HOST } from '../../config';

const mockSignIn = jest.fn();
let mockSlug = '';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('../QRScannerModal', () => () => null);
jest.mock('../../store/AuthContext', () => ({ useAuth: () => ({ signIn: mockSignIn }) }));
jest.mock('../../utils/preferences', () => ({ getLastCompanySlug: jest.fn().mockResolvedValue('') }));
jest.mock('../../api/client', () => ({
  adminLogin: jest.fn(),
  verifyAdminLogin: jest.fn(),
  employeeLogin: jest.fn(),
  setCompanySlug: jest.fn((slug) => { mockSlug = slug; }),
  getCompanySlug: jest.fn(() => mockSlug),
  parseCompanySlug: jest.requireActual('../../api/client').parseCompanySlug,
}));
// The first render compiles the screen and Expo modules; give a cold run time.
jest.setTimeout(30000);

beforeEach(() => {
  jest.clearAllMocks();
  mockSlug = '';
});

it('signs the admin in to the company typed in Company URL', async () => {
  api.adminLogin.mockImplementation(async () => {
    expect(mockSlug).toBe('acme'); // already applied when the request goes out
    return { data: { ok: true, token: 'tok', username: 'owner@acme.test', role: 'admin' } };
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  expect(screen.getByText('Company URL')).toBeTruthy();

  await fireEvent.changeText(screen.getByPlaceholderText(`${PORTAL_HOST}/your-company`), 'https://203.0.113.10/acme/login');
  await fireEvent.changeText(screen.getByPlaceholderText('Enter your email or username'), 'owner@acme.test');
  await fireEvent.changeText(screen.getByPlaceholderText('Enter your password'), 'password123');
  await fireEvent.press(screen.getByText('Sign In'));

  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(mockSignIn).toHaveBeenCalledWith('tok', {
    role: 'admin', adminRole: 'admin', name: 'owner@acme.test', companySlug: 'acme',
  });
});

it('a blank Company URL signs in to the default company', async () => {
  api.employeeLogin.mockResolvedValue({ data: { ok: true, token: 'tok', name: 'Asha', employee_id: 'EMP1' } });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await fireEvent.press(screen.getByText('Employee'));
  await fireEvent.changeText(screen.getByPlaceholderText('Enter your employee ID'), 'emp1');
  await fireEvent.changeText(screen.getByPlaceholderText('Enter your password'), 'password123');
  await fireEvent.press(screen.getByText('Sign In'));

  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(api.setCompanySlug).toHaveBeenCalledWith('');
  expect(mockSignIn.mock.calls[0][1].companySlug).toBe('');
});
