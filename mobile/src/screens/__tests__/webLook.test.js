// The screens the app opens on wear the website's look
// (src/theme/webAuthStyle.js): the launch screen is the landing hero, the
// login screen is the website's sign-in card.
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

import LaunchCountdownScreen from '../LaunchCountdownScreen';
import LoginScreen from '../LoginScreen';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('../QRScannerModal', () => () => null);
jest.mock('../../store/AuthContext', () => ({ useAuth: () => ({ signIn: jest.fn() }) }));
// The first render compiles the screens and Expo modules; give a cold run time.
jest.setTimeout(30000);

jest.mock('../../api/client', () => ({
  adminLogin: jest.fn(),
  verifyAdminLogin: jest.fn(),
  employeeLogin: jest.fn(),
}));

describe('LaunchCountdownScreen', () => {
  it('shows the website hero, and both Log In and Enter Portal continue', async () => {
    const onContinue = jest.fn();
    const screen = await render(<LaunchCountdownScreen onContinue={onContinue} />);
    expect(screen.getByText('HR, attendance, and payroll')).toBeTruthy();
    expect(screen.getByText('on one secure platform')).toBeTruthy();
    await fireEvent.press(screen.getByText('Log In'));
    await fireEvent.press(screen.getByText(/^Enter Portal/));
    expect(onContinue).toHaveBeenCalledTimes(2);
  });
});

describe('LoginScreen', () => {
  it("shows the website's sign-in card and links to company registration", async () => {
    const navigation = { navigate: jest.fn() };
    const screen = await render(<LoginScreen navigation={navigation} />);
    expect(screen.getByText('Sign In to Your Workspace')).toBeTruthy();
    expect(screen.getByText('Email or Username')).toBeTruthy();
    await fireEvent.press(screen.getByText('Register a Company'));
    expect(navigation.navigate).toHaveBeenCalledWith('CompanySignup');
  });
});
