// The employee's Policies screen shows the terms and policies their own
// company published, one tab per category, and how to reach the company.
import React from 'react';
import { Linking } from 'react-native';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import PoliciesScreen, { policyBullets, categoryTab } from '../PoliciesScreen';
import * as api from '../../../api/client';

jest.mock('../../../store/ThemeContext', () => ({
  useTheme: () => ({ colors: new Proxy({}, { get: () => '#0B5CAD' }) }),
}));
jest.mock('../../../components/profile/ProfileHeader', () => () => null);
jest.mock('../../../api/client', () => ({ fetchCompanyPolicies: jest.fn() }));
jest.setTimeout(30000);

const POLICIES = [
  { id: 1, category: 'terms', title: 'Terms of Work', body: 'Be on time.\n- Wear your ID card.' },
  { id: 2, category: 'Travel Policy', title: 'Travel', body: 'Book early.' },
];

beforeEach(() => jest.clearAllMocks());

describe('policy helpers', () => {
  it('turns a policy into bullets, one per line', () => {
    expect(policyBullets('First.\r\n\n  - Second.\n• Third\n* Fourth')).toEqual(['First.', 'Second.', 'Third', 'Fourth']);
    expect(policyBullets('')).toEqual([]);
  });

  it('names built-in categories and keeps the company\'s own', () => {
    expect(categoryTab('posh')).toBe('POSH');
    expect(categoryTab('Travel Policy')).toBe('Travel Policy');
  });
});

describe('PoliciesScreen', () => {
  it('shows the company\'s policies by category', async () => {
    api.fetchCompanyPolicies.mockResolvedValue({ data: { ok: true, policies: POLICIES, contact: null } });
    const screen = await render(<PoliciesScreen />);
    await waitFor(() => expect(screen.getByText('Terms of Work')).toBeTruthy());
    expect(screen.getByText('Wear your ID card.')).toBeTruthy();
    expect(screen.queryByText('Travel')).toBeNull();

    await fireEvent.press(screen.getByText('Travel Policy'));
    expect(screen.getByText('Book early.')).toBeTruthy();
    expect(screen.queryByText('Terms of Work')).toBeNull();
  });

  it('says so when nothing is published yet', async () => {
    api.fetchCompanyPolicies.mockResolvedValue({ data: { ok: true, policies: [], contact: null } });
    const screen = await render(<PoliciesScreen />);
    await waitFor(() => expect(screen.getByText('No policies yet')).toBeTruthy());
  });

  it('offers a retry when loading fails', async () => {
    api.fetchCompanyPolicies.mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ data: { ok: true, policies: POLICIES, contact: null } });
    const screen = await render(<PoliciesScreen />);
    await waitFor(() => expect(screen.getByText("Couldn't load policies")).toBeTruthy());
    await fireEvent.press(screen.getByText('Refresh'));
    await waitFor(() => expect(screen.getByText('Terms of Work')).toBeTruthy());
  });

  it('shows how to contact the company', async () => {
    jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    api.fetchCompanyPolicies.mockResolvedValue({ data: { ok: true, policies: POLICIES, contact: {
      company_name: 'Acme Corp', contact_phone: '+91 98765-43210', contact_email: 'hr@acme.in', website: '',
    } } });
    const screen = await render(<PoliciesScreen />);
    await waitFor(() => expect(screen.getByText('Contact Acme Corp about any policy.')).toBeTruthy());
    await fireEvent.press(screen.getByText('+91 98765-43210'));
    expect(Linking.openURL).toHaveBeenCalledWith('tel:+919876543210');
    await fireEvent.press(screen.getByText('hr@acme.in'));
    expect(Linking.openURL).toHaveBeenCalledWith('mailto:hr@acme.in');
  });
});
