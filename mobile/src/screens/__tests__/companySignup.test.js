// The company-signup screens follow the website's wizard: account (email,
// mobile, password) -> email code -> company profile -> documents.
import React from 'react';
import { Alert } from 'react-native';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import CompanySignupScreen from '../CompanySignupScreen';
import CompanyOtpVerifyScreen from '../CompanyOtpVerifyScreen';
import CompanyProfileScreen from '../CompanyProfileScreen';
import * as api from '../../api/client';

jest.mock('../../api/client', () => ({
  startCompanySignup: jest.fn(),
  verifyCompanySignupOtp: jest.fn(),
  resendCompanySignupOtp: jest.fn(),
  submitCompanyProfile: jest.fn(),
}));

jest.mock('../../store/ThemeContext', () => ({
  useTheme: () => ({
    colors: { card: '#fff', background: '#f8fafc', border: '#e2e8f0', text: '#0f172a', textLight: '#94a3b8', blueBg: '#eef2ff' },
  }),
}));

const navigation = () => ({ navigate: jest.fn(), goBack: jest.fn() });

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

describe('CompanySignupScreen (account step)', () => {
  it('sends just the email, mobile and password, then opens the code screen', async () => {
    api.startCompanySignup.mockResolvedValue({ data: { ok: true, application_id: 7, access_token: 'tok' } });
    const nav = navigation();
    const screen = await render(<CompanySignupScreen navigation={nav} />);

    await fireEvent.changeText(screen.getByPlaceholderText('you@company.com'), ' Owner@Acme.TEST ');
    await fireEvent.changeText(screen.getByPlaceholderText('+91 98765 43210'), '+91 98765-43210');
    await fireEvent.changeText(screen.getByPlaceholderText('At least 8 characters'), 'password123');
    await fireEvent.press(screen.getByText('Continue to Email Verification'));

    await waitFor(() => expect(nav.navigate).toHaveBeenCalled());
    expect(api.startCompanySignup).toHaveBeenCalledWith({
      admin_email: 'owner@acme.test', admin_mobile: '+919876543210', admin_password: 'password123',
    });
    expect(nav.navigate).toHaveBeenCalledWith('CompanyOtpVerify', {
      applicationId: 7, accessToken: 'tok', adminEmail: 'owner@acme.test',
    });
    expect(screen.queryByPlaceholderText('e.g. Acme Corporation')).toBeNull();
  });

  it('catches a bad mobile number before calling the server', async () => {
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fireEvent.changeText(screen.getByPlaceholderText('you@company.com'), 'owner@acme.test');
    await fireEvent.changeText(screen.getByPlaceholderText('+91 98765 43210'), '12345');
    await fireEvent.changeText(screen.getByPlaceholderText('At least 8 characters'), 'password123');
    await fireEvent.press(screen.getByText('Continue to Email Verification'));
    expect(api.startCompanySignup).not.toHaveBeenCalled();
    expect(Alert.alert).toHaveBeenCalledWith('Invalid Mobile Number', expect.any(String));
  });
});

describe('CompanyOtpVerifyScreen', () => {
  it('goes on to the company profile once the code checks out', async () => {
    api.verifyCompanySignupOtp.mockResolvedValue({ data: { ok: true, step: 'profile' } });
    const nav = navigation();
    const route = { params: { applicationId: 7, accessToken: 'tok', adminEmail: 'owner@acme.test' } };
    const screen = await render(<CompanyOtpVerifyScreen navigation={nav} route={route} />);

    await fireEvent.changeText(screen.getByPlaceholderText('••••••'), '123456');
    await fireEvent.press(screen.getByText('Verify & Continue'));

    await waitFor(() => expect(nav.navigate).toHaveBeenCalledWith('CompanyProfile', {
      applicationId: 7, accessToken: 'tok',
    }));
  });
});

describe('CompanyProfileScreen (company step)', () => {
  it('sends the profile with the chosen plan, then opens the documents', async () => {
    api.submitCompanyProfile.mockResolvedValue({ data: { ok: true, step: 'documents' } });
    const nav = navigation();
    const route = { params: { applicationId: 7, accessToken: 'tok' } };
    const screen = await render(<CompanyProfileScreen navigation={nav} route={route} />);

    await fireEvent.changeText(screen.getByPlaceholderText('e.g. Acme Corporation'), 'Acme Corp');
    await fireEvent.changeText(screen.getByPlaceholderText('27AAAAA0000A1Z5'), '27aaaaa0000a1z5');
    expect(screen.getByText('hrzest.com/acmecorp/login')).toBeTruthy();
    await fireEvent.press(screen.getByText('2-Day Free Trial'));
    await fireEvent.press(screen.getByText('Create My Company'));

    await waitFor(() => expect(nav.navigate).toHaveBeenCalledWith('CompanyDocumentUpload', {
      applicationId: 7, accessToken: 'tok', companyName: 'Acme Corp',
    }));
    const sent = api.submitCompanyProfile.mock.calls[0][0];
    // React Native's FormData keeps _parts; the test environment's may be the standard one.
    const fields = Object.fromEntries(sent._parts || Array.from(sent.entries()));
    expect(fields).toEqual({
      application_id: '7', access_token: 'tok', company_name: 'Acme Corp',
      gst_number: '27AAAAA0000A1Z5', subdomain: 'acmecorp', signup_plan: 'trial',
    });
  });

  it('shows the server message when the profile is refused', async () => {
    api.submitCompanyProfile.mockRejectedValue({ response: { data: { ok: false, msg: "Subdomain 'acme' is already taken." } } });
    const nav = navigation();
    const screen = await render(<CompanyProfileScreen navigation={nav} route={{ params: { applicationId: 7, accessToken: 'tok' } }} />);

    await fireEvent.changeText(screen.getByPlaceholderText('e.g. Acme Corporation'), 'Acme');
    await fireEvent.changeText(screen.getByPlaceholderText('27AAAAA0000A1Z5'), '27AAAAA0000A1Z5');
    await fireEvent.press(screen.getByText('Create My Company'));

    await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith(
      'Could Not Create Company', "Subdomain 'acme' is already taken."));
    expect(nav.navigate).not.toHaveBeenCalled();
  });
});
