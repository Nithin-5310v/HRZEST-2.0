// Company signup is one screen, like the website's: the company name and
// the admin's account, then the emailed code on the same card -- which
// creates the company and signs the admin straight in, under the new
// company's URL slug.
import React from 'react';
import { Alert } from 'react-native';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import CompanySignupScreen from '../CompanySignupScreen';
import * as api from '../../api/client';
import { API_BASE_URL } from '../../config';

const mockSignIn = jest.fn();

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('../../store/AuthContext', () => ({ useAuth: () => ({ signIn: mockSignIn }) }));
jest.mock('../../api/client', () => ({
  startCompanySignup: jest.fn(),
  verifyCompanySignupOtp: jest.fn(),
  resendCompanySignupOtp: jest.fn(),
}));
// The first render compiles the screen and Expo modules; give a cold run time.
jest.setTimeout(30000);

const navigation = () => ({ navigate: jest.fn(), goBack: jest.fn() });

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

const fillSignup = async (screen, mobile = '+91 98765-43210') => {
  await fireEvent.changeText(screen.getByPlaceholderText('e.g. Acme Corporation'), '  Acme   Corp ');
  await fireEvent.changeText(screen.getByPlaceholderText('you@company.com'), ' Owner@Acme.TEST ');
  await fireEvent.changeText(screen.getByPlaceholderText('+91 98765 43210'), mobile);
  await fireEvent.changeText(screen.getByPlaceholderText('At least 8 characters'), 'password123');
};

describe('CompanySignupScreen', () => {
  it('previews the company URL made from the name', async () => {
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fireEvent.changeText(screen.getByPlaceholderText('e.g. Acme Corporation'), 'Acme Pvt. Ltd.');
    // The server's own address -- the API and the website are one server.
    expect(screen.getByText(`Your team's portal: ${API_BASE_URL}/acme-pvt-ltd/login`)).toBeTruthy();
  });

  it('sends the company name and account, then asks for the code on the same card', async () => {
    api.startCompanySignup.mockResolvedValue({ data: { ok: true, application_id: 7, access_token: 'tok' } });
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fillSignup(screen);
    await fireEvent.press(screen.getByText('Send Verification Code'));

    await waitFor(() => expect(screen.getByText('Verify & Open My Dashboard')).toBeTruthy());
    expect(api.startCompanySignup).toHaveBeenCalledWith({
      company_name: 'Acme Corp', admin_email: 'owner@acme.test', admin_mobile: '+919876543210',
      admin_password: 'password123',
    });
    expect(screen.getByText('Acme Corp')).toBeTruthy();
    expect(screen.getByText('owner@acme.test')).toBeTruthy();
    expect(screen.queryByPlaceholderText('At least 8 characters')).toBeNull();
  });

  it('signs the admin into the new company once the code checks out', async () => {
    api.startCompanySignup.mockResolvedValue({ data: { ok: true, application_id: 7, access_token: 'tok' } });
    api.verifyCompanySignupOtp.mockResolvedValue({ data: {
      ok: true, step: 'done', token: 'bearer-1', username: 'owner@acme.test', role: 'admin',
      company_slug: 'acme-corp', company_name: 'Acme Corp',
    } });
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fillSignup(screen);
    await fireEvent.press(screen.getByText('Send Verification Code'));
    await waitFor(() => expect(screen.getByPlaceholderText('000000')).toBeTruthy());

    await fireEvent.changeText(screen.getByPlaceholderText('000000'), '12 34 56');
    await fireEvent.press(screen.getByText('Verify & Open My Dashboard'));

    await waitFor(() => expect(mockSignIn).toHaveBeenCalled());
    expect(api.verifyCompanySignupOtp).toHaveBeenCalledWith(7, 'tok', '123456');
    expect(mockSignIn).toHaveBeenCalledWith('bearer-1', {
      role: 'admin', adminRole: 'admin', name: 'owner@acme.test', company: 'Acme Corp', companySlug: 'acme-corp',
    });
  });

  it('keeps the code step when the code is wrong', async () => {
    api.startCompanySignup.mockResolvedValue({ data: { ok: true, application_id: 7, access_token: 'tok' } });
    api.verifyCompanySignupOtp.mockRejectedValue({ response: { data: { ok: false, msg: 'Incorrect code. Please try again.' } } });
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fillSignup(screen);
    await fireEvent.press(screen.getByText('Send Verification Code'));
    await waitFor(() => expect(screen.getByPlaceholderText('000000')).toBeTruthy());

    await fireEvent.changeText(screen.getByPlaceholderText('000000'), '000000');
    await fireEvent.press(screen.getByText('Verify & Open My Dashboard'));

    await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith('Could Not Verify', 'Incorrect code. Please try again.'));
    expect(mockSignIn).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText('000000')).toBeTruthy();
  });

  it('"Change details" goes back to the form', async () => {
    api.startCompanySignup.mockResolvedValue({ data: { ok: true, application_id: 7, access_token: 'tok' } });
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fillSignup(screen);
    await fireEvent.press(screen.getByText('Send Verification Code'));
    await waitFor(() => expect(screen.getByText('Change details')).toBeTruthy());
    await fireEvent.press(screen.getByText('Change details'));
    expect(screen.getByText('Send Verification Code')).toBeTruthy();
  });

  it('catches a bad mobile number before calling the server', async () => {
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fillSignup(screen, '12345');
    await fireEvent.press(screen.getByText('Send Verification Code'));
    expect(api.startCompanySignup).not.toHaveBeenCalled();
    expect(Alert.alert).toHaveBeenCalledWith('Invalid Mobile Number', expect.any(String));
  });

  it('needs a company name with a letter or digit in it', async () => {
    const screen = await render(<CompanySignupScreen navigation={navigation()} />);
    await fillSignup(screen);
    await fireEvent.changeText(screen.getByPlaceholderText('e.g. Acme Corporation'), '!!!');
    await fireEvent.press(screen.getByText('Send Verification Code'));
    expect(api.startCompanySignup).not.toHaveBeenCalled();
    expect(Alert.alert).toHaveBeenCalledWith('Invalid Company Name', expect.any(String));
  });
});
