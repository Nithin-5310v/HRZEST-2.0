// The screens the app opens on wear the app's one look: the landing page
// and the sign-in page, both under the shared top bar (components/AppTopBar.js).
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

import LaunchCountdownScreen from '../LaunchCountdownScreen';
import LoginScreen from '../LoginScreen';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  // components/SafeAreaView.js wraps this one.
  SafeAreaView: ({ children, ...props }) => {
    const { View } = require('react-native');
    return <View {...props}>{children}</View>;
  },
}));
jest.mock('../QRScannerModal', () => () => null);
jest.mock('../../store/AuthContext', () => ({ useAuth: () => ({ signIn: jest.fn(), user: null }) }));
jest.mock('../../utils/preferences', () => ({ getLastCompanySlug: jest.fn().mockResolvedValue('') }));
// The first render compiles the screens and Expo modules; give a cold run time.
jest.setTimeout(30000);

jest.mock('../../api/client', () => ({
  adminLogin: jest.fn(),
  verifyAdminLogin: jest.fn(),
  employeeLogin: jest.fn(),
  signInAccount: jest.fn(),
}));

describe('LaunchCountdownScreen', () => {
  it('shows the headline, and Sign in / Register go to their pages', async () => {
    const navigation = { navigate: jest.fn() };
    const screen = await render(<LaunchCountdownScreen navigation={navigation} />);
    expect(screen.getByText(/HR, attendance and payroll/)).toBeTruthy();
    expect(screen.getByText('on one secure platform')).toBeTruthy();
    // The top bar's link and the main button.
    const signIns = screen.getAllByText('Sign in');
    expect(signIns).toHaveLength(2);
    await fireEvent.press(signIns[0]);
    await fireEvent.press(signIns[1]);
    expect(navigation.navigate).toHaveBeenNthCalledWith(1, 'Login');
    expect(navigation.navigate).toHaveBeenNthCalledWith(2, 'Login');
    await fireEvent.press(screen.getByText('Register your company'));
    expect(navigation.navigate).toHaveBeenLastCalledWith('CompanySignup');
  });
});

describe('LoginScreen', () => {
  it('has one box for any account, a back arrow, and links to company registration', async () => {
    const navigation = { navigate: jest.fn(), canGoBack: () => true, goBack: jest.fn() };
    const screen = await render(<LoginScreen navigation={navigation} />);
    expect(screen.getByText('Welcome back')).toBeTruthy();
    expect(screen.getByText('EMAIL, USERNAME OR EMPLOYEE ID')).toBeTruthy();
    expect(screen.queryByText('Employee')).toBeNull();
    await fireEvent.press(screen.getByText('Register your company'));
    expect(navigation.navigate).toHaveBeenCalledWith('CompanySignup');
    await fireEvent.press(screen.getByLabelText('Back'));
    expect(navigation.goBack).toHaveBeenCalled();
  });
});
