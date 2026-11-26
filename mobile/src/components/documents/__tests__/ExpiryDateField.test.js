// Document upload > optional expiry date. The picker is the system date
// dialog (@react-native-community/datetimepicker): "set" fills the field as
// YYYY-MM-DD in the phone's time zone, "dismissed" leaves it as it was.
import React, { useState } from 'react';
import { render, fireEvent } from '@testing-library/react-native';

import ExpiryDateField from '../ExpiryDateField';

jest.mock('@react-native-community/datetimepicker', () => {
  const { View } = require('react-native');
  return (props) => <View {...props} />;
});
jest.setTimeout(30000);

const COLORS = new Proxy({}, { get: () => '#2563EB' });

function Harness({ initial = '', onValue = () => {} }) {
  const [value, setValue] = useState(initial);
  return (
    <ExpiryDateField
      value={value}
      onChange={(v) => { setValue(v); onValue(v); }}
      colors={COLORS}
    />
  );
}

test('no date until one is picked; Cancel keeps it empty', async () => {
  const onValue = jest.fn();
  const screen = await render(<Harness onValue={onValue} />);
  expect(screen.getByText('No expiry date')).toBeTruthy();
  expect(screen.queryByTestId('documentExpiryPicker')).toBeNull();

  await fireEvent.press(screen.getByTestId('documentExpiryButton'));
  const picker = screen.getByTestId('documentExpiryPicker');
  expect(picker.props.mode).toBe('date');
  await fireEvent(picker, 'change', { type: 'dismissed' }, undefined);

  expect(screen.queryByTestId('documentExpiryPicker')).toBeNull();
  expect(screen.getByText('No expiry date')).toBeTruthy();
  expect(onValue).not.toHaveBeenCalled();
});

test('a picked date is sent as the local YYYY-MM-DD and can be cleared', async () => {
  const onValue = jest.fn();
  const screen = await render(<Harness onValue={onValue} />);

  await fireEvent.press(screen.getByTestId('documentExpiryButton'));
  // 00:30 local on 12 March: toISOString() would say 11 March in India.
  await fireEvent(screen.getByTestId('documentExpiryPicker'), 'change', { type: 'set' }, new Date(2027, 2, 12, 0, 30));

  expect(onValue).toHaveBeenLastCalledWith('2027-03-12');
  expect(screen.getByText('2027-03-12')).toBeTruthy();

  await fireEvent.press(screen.getByTestId('documentExpiryClear'));
  expect(onValue).toHaveBeenLastCalledWith('');
  expect(screen.getByText('No expiry date')).toBeTruthy();
});

test('reopening the picker starts from the chosen date', async () => {
  const screen = await render(<Harness initial="2028-01-31" />);
  await fireEvent.press(screen.getByTestId('documentExpiryButton'));
  const value = screen.getByTestId('documentExpiryPicker').props.value;
  expect([value.getFullYear(), value.getMonth(), value.getDate()]).toEqual([2028, 0, 31]);
});
