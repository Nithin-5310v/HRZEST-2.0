// The sign-in page: one box for an email, username or employee ID (the
// server's /api/signin decides admin or employee, like the website), sent
// to the company typed in the Company box -- applied (api/client.js's
// setCompanySlug) before the call, and remembered by the session
// (signIn's companySlug).
import React from 'react';
import { Alert } from 'react-native';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import LoginScreen from '../LoginScreen';
import * as api from '../../api/client';

const mockSignIn = jest.fn();
let mockSlug = '';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('../QRScannerModal', () => () => null);
jest.mock('../../store/AuthContext', () => ({ useAuth: () => ({ signIn: mockSignIn, user: null }) }));
jest.mock('../../utils/preferences', () => ({ getLastCompanySlug: jest.fn().mockResolvedValue('') }));
jest.mock('../../api/client', () => ({
  adminLogin: jest.fn(),
  verifyAdminLogin: jest.fn(),
  employeeLogin: jest.fn(),
  signInAccount: jest.fn(),
  setCompanySlug: jest.fn((slug) => { mockSlug = slug; }),
  getCompanySlug: jest.fn(() => mockSlug),
  parseCompanySlug: jest.requireActual('../../api/client').parseCompanySlug,
  // The server's lookup (/api/company/resolve); by default it agrees with the typed slug.
  resolveCompany: jest.fn(async (q) => ({
    data: { ok: true, slug: jest.requireActual('../../api/client').parseCompanySlug(q) },
  })),
}));
// The first render compiles the screen and Expo modules; give a cold run time.
jest.setTimeout(30000);

const COMPANY = "Company name or web address";
const IDENT = 'e.g. you@company.com or EMP-1001';
const PASS = 'Your password';

beforeEach(() => {
  jest.clearAllMocks();
  mockSlug = '';
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

async function signInWith(screen, { company, ident, password = 'password123' }) {
  if (company !== undefined) await fireEvent.changeText(screen.getByPlaceholderText(COMPANY), company);
  await fireEvent.changeText(screen.getByPlaceholderText(IDENT), ident);
  await fireEvent.changeText(screen.getByPlaceholderText(PASS), password);
  await fireEvent.press(screen.getAllByText('Sign in').at(-1)); // the button, not the page title
}

it('signs an admin in to the company typed in the Company box', async () => {
  api.signInAccount.mockImplementation(async () => {
    expect(mockSlug).toBe('acme'); // already applied when the request goes out
    return { data: { ok: true, account: 'admin', token: 'tok', username: 'owner@acme.test', role: 'admin' } };
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { company: 'https://203.0.113.10/acme/login', ident: 'owner@acme.test' });

  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(api.signInAccount).toHaveBeenCalledWith('owner@acme.test', 'password123');
  expect(mockSignIn).toHaveBeenCalledWith('tok', {
    role: 'admin', adminRole: 'admin', name: 'owner@acme.test', companySlug: 'acme',
  });
});

it('signs an employee in from the same box', async () => {
  api.signInAccount.mockResolvedValue({
    data: { ok: true, account: 'employee', token: 'tok', name: 'Asha', employee_id: 'EMP1', email: 'asha@acme.test' },
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { ident: 'emp1' });

  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(api.setCompanySlug).toHaveBeenCalledWith('');
  const [token, user] = mockSignIn.mock.calls[0];
  expect(token).toBe('tok');
  expect(user).toMatchObject({ role: 'employee', employeeId: 'EMP1', name: 'Asha', companySlug: '' });
});

it('asks for the emailed code when the admin account needs one', async () => {
  api.signInAccount.mockResolvedValue({ data: { ok: true, account: 'admin', mfa_required: true, challenge_id: 'ch1' } });
  api.verifyAdminLogin.mockResolvedValue({ data: { ok: true, token: 'tok', username: 'owner', role: 'hr' } });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { ident: 'owner' });

  await waitFor(() => expect(screen.getByText('VERIFICATION CODE')).toBeTruthy());
  await fireEvent.changeText(screen.getByPlaceholderText('000000'), '123456');
  await fireEvent.press(screen.getByText('Verify and continue'));
  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(api.verifyAdminLogin).toHaveBeenCalledWith('ch1', '123456');
  expect(mockSignIn.mock.calls[0][1]).toMatchObject({ role: 'admin', adminRole: 'hr' });
});

it('still signs in against a server without /api/signin (admin, then employee)', async () => {
  api.signInAccount.mockRejectedValue({ response: { status: 404, data: { ok: false, msg: 'Page Not Found' } } });
  api.adminLogin.mockRejectedValue({ response: { status: 401, data: { ok: false, msg: 'Invalid credentials' } } });
  api.employeeLogin.mockResolvedValue({ data: { ok: true, token: 'tok', name: 'Asha', employee_id: 'EMP1' } });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { ident: 'emp1' });

  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(api.adminLogin).toHaveBeenCalledWith('emp1', 'password123');
  expect(api.employeeLogin).toHaveBeenCalledWith('EMP1', 'password123');
  expect(mockSignIn.mock.calls[0][1]).toMatchObject({ role: 'employee', employeeId: 'EMP1' });
});

it('signs in to the company the server finds for a typed company name', async () => {
  api.resolveCompany.mockResolvedValueOnce({ data: { ok: true, slug: 'acme-foods' } });
  api.signInAccount.mockImplementation(async () => {
    expect(mockSlug).toBe('acme-foods');
    return { data: { ok: true, account: 'admin', token: 'tok', username: 'owner@acme.test', role: 'admin' } };
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { company: 'Acme Foods', ident: 'owner@acme.test' });

  await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
  expect(api.resolveCompany).toHaveBeenCalledWith('Acme Foods');
  expect(mockSignIn.mock.calls[0][1].companySlug).toBe('acme-foods');
});

it('says the company is not found -- and sends no password -- for an unknown company', async () => {
  api.resolveCompany.mockRejectedValueOnce({
    response: { status: 404, data: { ok: false, msg: 'No such company.' } },
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { company: 'no-such-co', ident: 'owner@acme.test' });

  await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith('Company Not Found', 'No such company.'));
  expect(api.signInAccount).not.toHaveBeenCalled();
});

it('tells a wrong password apart from a network ban', async () => {
  api.signInAccount.mockRejectedValueOnce({ response: { status: 401, data: { ok: false, msg: 'Invalid credentials' } } });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { company: 'acme', ident: 'owner@acme.test', password: 'wrong-pass' });
  await waitFor(() => expect(Alert.alert).toHaveBeenCalled());
  expect(Alert.alert.mock.calls[0][1]).toMatch(/Wrong email, username, employee ID or password/);

  api.signInAccount.mockRejectedValueOnce({ response: { status: 403, data: { ok: false, msg: 'Access denied.' } } });
  await fireEvent.press(screen.getAllByText('Sign in').at(-1)); // the button, not the page title
  await waitFor(() => expect(Alert.alert).toHaveBeenCalledTimes(2));
  expect(Alert.alert.mock.calls[1][1]).toMatch(/paused sign-ins from this network/);
});

it('shows the server message when an admin reset the password', async () => {
  const msg = 'Your password was reset by your administrator. Sign in on the web portal to set a new password.';
  api.signInAccount.mockRejectedValueOnce({
    response: { status: 403, data: { ok: false, password_change_required: true, msg } },
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { ident: 'EMP1' });
  await waitFor(() => expect(Alert.alert).toHaveBeenCalled());
  expect(Alert.alert.mock.calls[0][1]).toBe(msg);
});

it('tells a leaver their access ended after the last working day', async () => {
  const msg = 'Your access ended after your last working day (31 Oct 2026). Your records are kept.';
  api.signInAccount.mockRejectedValueOnce({
    response: { status: 403, data: { ok: false, access_ended: true, msg } },
  });
  const screen = await render(<LoginScreen navigation={{ navigate: jest.fn() }} />);
  await signInWith(screen, { ident: 'EMP1' });
  await waitFor(() => expect(Alert.alert).toHaveBeenCalled());
  expect(Alert.alert.mock.calls[0]).toEqual(['Access Ended', msg]);
  expect(mockSignIn).not.toHaveBeenCalled();
});
