// Admin > More > Organization: the company's details (editable, as on
// the website's Organization page) and its own terms & policies.
import React from 'react';
import { Alert } from 'react-native';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import OrganizationScreen from '../OrganizationScreen';
import * as api from '../../../api/client';

jest.mock('../../../store/ThemeContext', () => ({
  useTheme: () => ({ colors: new Proxy({}, { get: () => '#0B5CAD' }) }),
}));
jest.mock('../../../components/admin/AdminHeader', () => () => null);
jest.mock('expo-image-picker', () => ({}));
jest.mock('../../../api/client', () => ({
  fetchOrganization: jest.fn(),
  saveOrganization: jest.fn(),
  uploadOrganizationLogo: jest.fn(),
  fetchAdminPolicies: jest.fn(),
  addPolicy: jest.fn(),
  updatePolicy: jest.fn(),
  setPolicyPublished: jest.fn(),
  deletePolicy: jest.fn(),
}));
jest.setTimeout(30000);

const ORG = {
  company_name: 'Acme Corp', company_tagline: 'We make things', company_code: 'ACME',
  contact_phone: '+91 98765 43210', contact_email: '', website: '', address: '1 Main Road', logo_url: '',
};
const POLICIES = [
  { id: 1, category: 'terms', title: 'Terms of Work', body: 'Be on time.', is_published: true },
  { id: 2, category: 'Travel Policy', title: 'Travel', body: 'Book early.', is_published: false },
];
const CATEGORIES = [{ key: 'terms', label: 'Terms & Conditions' }, { key: 'rules', label: 'Rules & Regulations' }];
const TEMPLATES = [{ category: 'rules', title: 'Rules & Regulations', body: 'Template rules.' }];

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  api.fetchOrganization.mockResolvedValue({ data: { ok: true, organization: ORG, verification: 'approved' } });
  api.fetchAdminPolicies.mockResolvedValue({
    data: { ok: true, policies: POLICIES, categories: CATEGORIES, templates: TEMPLATES },
  });
});

const openScreen = async () => {
  const screen = await render(<OrganizationScreen navigation={{ navigate: jest.fn() }} />);
  await waitFor(() => expect(screen.getByText('Company details')).toBeTruthy());
  return screen;
};

describe('OrganizationScreen details', () => {
  it('shows the company details, with empty ones marked', async () => {
    const screen = await openScreen();
    expect(screen.getAllByText('We make things')).toHaveLength(2); // header card + details row
    expect(screen.getByText('1 Main Road')).toBeTruthy();
    expect(screen.getAllByText('Not added')).toHaveLength(2); // email, website
  });

  it('edits and saves the details', async () => {
    api.saveOrganization.mockResolvedValue({ data: {
      ok: true, msg: 'Organization details saved.',
      organization: { ...ORG, contact_email: 'hr@acme.in', website: 'https://acme.in' },
    } });
    const screen = await openScreen();
    await fireEvent.press(screen.getByText('Edit'));
    await fireEvent.changeText(screen.getByPlaceholderText('hr@yourcompany.com'), 'hr@acme.in');
    await fireEvent.changeText(screen.getByPlaceholderText('www.yourcompany.com'), 'acme.in');
    await fireEvent.press(screen.getByText('Save changes'));

    await waitFor(() => expect(screen.getByText('https://acme.in')).toBeTruthy());
    expect(api.saveOrganization).toHaveBeenCalledWith({
      company_name: 'Acme Corp', company_tagline: 'We make things', contact_phone: '+91 98765 43210',
      contact_email: 'hr@acme.in', website: 'acme.in', address: '1 Main Road',
    });
    expect(screen.queryByText('Save changes')).toBeNull();
  });

  it("keeps the form open and shows the server's message when saving fails", async () => {
    api.saveOrganization.mockRejectedValue({ response: { data: { ok: false, msg: 'Enter a valid email address.' } } });
    const screen = await openScreen();
    await fireEvent.press(screen.getByText('Edit'));
    await fireEvent.press(screen.getByText('Save changes'));
    await waitFor(() => expect(Alert.alert).toHaveBeenCalledWith('Could Not Save', 'Enter a valid email address.'));
    expect(screen.getByText('Save changes')).toBeTruthy();
  });

  it('does not send a blank name', async () => {
    const screen = await openScreen();
    await fireEvent.press(screen.getByText('Edit'));
    await fireEvent.changeText(screen.getByPlaceholderText("Your company's name"), '  ');
    await fireEvent.press(screen.getByText('Save changes'));
    expect(api.saveOrganization).not.toHaveBeenCalled();
    expect(Alert.alert).toHaveBeenCalledWith('Name Required', expect.any(String));
  });
});

describe('OrganizationScreen terms & policies', () => {
  const openPolicies = async () => {
    const screen = await openScreen();
    await fireEvent.press(screen.getByText('Terms & Policies'));
    return screen;
  };

  it('lists every policy with its category and status', async () => {
    const screen = await openPolicies();
    expect(screen.getByText('Terms of Work')).toBeTruthy();
    expect(screen.getByText('Terms & Conditions')).toBeTruthy(); // built-in label
    expect(screen.getByText('Travel Policy')).toBeTruthy();       // the company's own category
    expect(screen.getByText('Published')).toBeTruthy();
    expect(screen.getByText('Draft')).toBeTruthy();
  });

  it('adds a policy from a template', async () => {
    api.addPolicy.mockResolvedValue({ data: { ok: true, id: 3 } });
    const screen = await openPolicies();
    await fireEvent.press(screen.getByText('Add a policy'));
    await fireEvent.press(screen.getAllByText('Rules & Regulations')[0]); // the template chip
    await fireEvent.press(screen.getByText('Save policy'));

    await waitFor(() => expect(api.addPolicy).toHaveBeenCalledWith({
      category: 'rules', title: 'Rules & Regulations', body: 'Template rules.', is_published: true,
    }));
    expect(api.fetchAdminPolicies).toHaveBeenCalledTimes(2); // reloaded after saving
  });

  it('adds a draft in a category of its own', async () => {
    api.addPolicy.mockResolvedValue({ data: { ok: true, id: 3 } });
    const screen = await openPolicies();
    await fireEvent.press(screen.getByText('Add a policy'));
    await fireEvent.press(screen.getByText('Other…'));
    await fireEvent.changeText(screen.getByPlaceholderText('e.g. Travel Policy, IT & Security'), 'IT & Security');
    await fireEvent.changeText(screen.getByPlaceholderText('e.g. Leave Policy 2026'), 'Laptops');
    await fireEvent.changeText(screen.getByPlaceholderText('Write the policy here…'), 'Lock your screen.');
    await fireEvent(screen.getByLabelText('Publish now'), 'valueChange', false);
    await fireEvent.press(screen.getByText('Save policy'));

    await waitFor(() => expect(api.addPolicy).toHaveBeenCalledWith({
      category: 'IT & Security', title: 'Laptops', body: 'Lock your screen.', is_published: false,
    }));
  });

  it('needs a title and text', async () => {
    const screen = await openPolicies();
    await fireEvent.press(screen.getByText('Add a policy'));
    await fireEvent.press(screen.getByText('Save policy'));
    expect(screen.getByText('Category, title and policy text are all required.')).toBeTruthy();
    expect(api.addPolicy).not.toHaveBeenCalled();
  });

  it('edits, publishes and deletes', async () => {
    api.updatePolicy.mockResolvedValue({ data: { ok: true } });
    api.setPolicyPublished.mockResolvedValue({ data: { ok: true } });
    api.deletePolicy.mockResolvedValue({ data: { ok: true } });
    const screen = await openPolicies();

    await fireEvent.press(screen.getAllByText('Edit')[1]);
    expect(screen.getByDisplayValue('Travel Policy')).toBeTruthy(); // custom category kept
    await fireEvent.changeText(screen.getByDisplayValue('Book early.'), 'Book two weeks early.');
    await fireEvent.press(screen.getByText('Save policy'));
    await waitFor(() => expect(api.updatePolicy).toHaveBeenCalledWith(2, {
      category: 'Travel Policy', title: 'Travel', body: 'Book two weeks early.',
    }));

    await waitFor(() => expect(screen.getByText('Publish')).toBeTruthy());
    await fireEvent.press(screen.getByText('Publish'));
    expect(api.setPolicyPublished).toHaveBeenCalledWith(2, true);
    await waitFor(() => expect(screen.queryByText('Draft')).toBeNull());

    await fireEvent.press(screen.getAllByText('Delete')[0]);
    const [, , buttons] = Alert.alert.mock.calls.find(([title]) => title === 'Delete Policy');
    await buttons.find((b) => b.text === 'Delete').onPress();
    expect(api.deletePolicy).toHaveBeenCalledWith(1);
    await waitFor(() => expect(screen.queryByText('Terms of Work')).toBeNull());
  });
});
