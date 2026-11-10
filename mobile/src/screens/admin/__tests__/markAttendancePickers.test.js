// Admin > Mark Attendance: the date and time pop-ups use Expo SDK's own
// picker (@react-native-community/datetimepicker). Rendering it opens the
// system dialog; onChange reports "set" (OK) or "dismissed" (Cancel).
import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

import MarkAttendanceScreen from '../MarkAttendanceScreen';
import * as api from '../../../api/client';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  SafeAreaView: ({ children, ...props }) => {
    const { View } = require('react-native');
    return <View {...props}>{children}</View>;
  },
}));
jest.mock('../../../store/ThemeContext', () => ({
  useTheme: () => ({ colors: new Proxy({}, { get: () => '#2563EB' }) }),
}));
jest.mock('../../../components/admin/AdminHeader', () => () => null);
jest.mock('expo-linear-gradient', () => {
  const { View } = require('react-native');
  return { LinearGradient: ({ children, ...props }) => <View {...props}>{children}</View> };
});
jest.mock('@react-native-picker/picker', () => {
  const { View } = require('react-native');
  const Picker = ({ children }) => <View>{children}</View>;
  Picker.Item = () => null;
  return { Picker };
});
// The real picker is a native dialog; a stand-in that keeps its props.
jest.mock('@react-native-community/datetimepicker', () => {
  const { View } = require('react-native');
  return (props) => <View {...props} />;
});
jest.mock('../../../api/client', () => ({
  fetchAttendanceEmployees: jest.fn(),
  fetchEmployees: jest.fn(),
  markAttendance: jest.fn(),
}));
jest.setTimeout(30000);

const EMP = { employee_id: 'EMP001', name: 'Asha Rao', shift_start: '09:00', shift_end: '18:00' };

beforeEach(() => {
  jest.clearAllMocks();
  api.fetchAttendanceEmployees.mockResolvedValue({ data: { ok: true, employees: [EMP] } });
});

async function renderScreen() {
  const screen = await render(<MarkAttendanceScreen navigation={{ dispatch: jest.fn() }} />);
  await waitFor(() => expect(screen.getByText('09:00')).toBeTruthy());
  return screen;
}

const isoOf = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

test('choosing a date reloads that day; Cancel keeps the current one', async () => {
  const screen = await renderScreen();
  expect(screen.queryByTestId('attendanceDatePicker')).toBeNull();

  // The date button shows the chosen day, e.g. "Tuesday, November 10, 2026".
  await fireEvent.press(screen.getByText(/\d{4}/));
  const picker = screen.getByTestId('attendanceDatePicker');
  expect(picker.props.mode).toBe('date');
  expect(picker.props.maximumDate).toBeInstanceOf(Date);

  await fireEvent(picker, 'change', { type: 'dismissed' }, undefined);
  expect(screen.queryByTestId('attendanceDatePicker')).toBeNull();
  expect(api.fetchAttendanceEmployees).toHaveBeenCalledTimes(1);

  await fireEvent.press(screen.getByText(/\d{4}/));
  const chosen = new Date(2026, 9, 5);
  await fireEvent(screen.getByTestId('attendanceDatePicker'), 'change', { type: 'set' }, chosen);
  expect(screen.queryByTestId('attendanceDatePicker')).toBeNull();
  await waitFor(() => expect(api.fetchAttendanceEmployees).toHaveBeenLastCalledWith(isoOf(chosen)));
});

test('a time set in the dialog fills the login time; Cancel leaves it', async () => {
  const screen = await renderScreen();

  await fireEvent.press(screen.getByText('09:00'));
  let picker = screen.getByTestId('attendanceTimePicker');
  expect(picker.props.mode).toBe('time');
  expect(picker.props.is24Hour).toBe(true);
  expect(picker.props.value.getHours()).toBe(9);

  await fireEvent(picker, 'change', { type: 'dismissed' }, undefined);
  expect(screen.queryByTestId('attendanceTimePicker')).toBeNull();
  expect(screen.getByText('09:00')).toBeTruthy();

  await fireEvent.press(screen.getByText('09:00'));
  picker = screen.getByTestId('attendanceTimePicker');
  await fireEvent(picker, 'change', { type: 'set' }, new Date(2000, 0, 1, 9, 45));
  expect(screen.queryByTestId('attendanceTimePicker')).toBeNull();
  expect(screen.getByText('09:45')).toBeTruthy();
});
