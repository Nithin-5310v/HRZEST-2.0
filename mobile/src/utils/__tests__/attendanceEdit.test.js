import {
  hhmm, initialEntry, isChanged, isMarked, buildRecord, savedSummary,
} from '../attendanceEdit';

const marked = {
  employee_id: 'E1', marked: true, attendance_type: 'Full Day',
  login_time: '09:41:00.722772', logout_time: '18:12', status: 'Late Login',
  shift_start: '09:00', shift_end: '18:00',
};
const fresh = { employee_id: 'E2', attendance_type: '', login_time: null, shift_start: '09:00', shift_end: '18:00' };

describe('attendanceEdit', () => {
  it('hhmm keeps hours and minutes only', () => {
    expect(hhmm('09:41:00.722772')).toBe('09:41');
    expect(hhmm('9:05')).toBe('09:05');
    expect(hhmm(null)).toBe('');
    expect(hhmm('soon')).toBe('');
  });

  it("starts a marked employee from their saved record, not the shift", () => {
    expect(initialEntry(marked)).toEqual({ type: 'Full Day', login: '09:41', logout: '18:12' });
    expect(isMarked(marked)).toBe(true);
  });

  it('starts an unmarked employee blank, with the shift times ready', () => {
    expect(initialEntry(fresh)).toEqual({ type: '', login: '09:00', logout: '18:00' });
    expect(isMarked(fresh)).toBe(false);
  });

  it('a saved record is unchanged until the admin edits it', () => {
    expect(isChanged(initialEntry(marked), marked)).toBe(false);
    expect(isChanged({ ...initialEntry(marked), logout: '19:00' }, marked)).toBe(true);
    expect(isChanged({ ...initialEntry(marked), type: 'Half Day' }, marked)).toBe(true);
  });

  it('an unmarked employee counts as changed once a type is picked', () => {
    expect(isChanged(initialEntry(fresh), fresh)).toBe(false);
    expect(isChanged({ ...initialEntry(fresh), type: 'Full Day' }, fresh)).toBe(true);
  });

  it('a check-in with no type yet can be given one (update)', () => {
    const scanned = { employee_id: 'E3', login_time: '10:05:00', status: 'Half Day Login', attendance_type: null };
    expect(isMarked(scanned)).toBe(true);
    expect(isChanged({ ...initialEntry(scanned), type: 'Full Day' }, scanned)).toBe(true);
  });

  it('sends no times for Absent, Leave, Holiday and Week Off', () => {
    for (const type of ['Absent', 'Leave', 'Holiday', 'Week Off']) {
      expect(buildRecord('E1', { type, login: '09:00', logout: '18:00' }))
        .toEqual({ emp_id: 'E1', attendance_type: type, login_time: null, logout_time: null });
    }
    expect(buildRecord('E1', { type: 'Full Day', login: '09:00', logout: '' }))
      .toEqual({ emp_id: 'E1', attendance_type: 'Full Day', login_time: '09:00', logout_time: null });
  });

  it('time edits on a no-time type are not a change', () => {
    const absent = { employee_id: 'E4', marked: true, attendance_type: 'Absent' };
    expect(isChanged({ type: 'Absent', login: '09:00', logout: '18:00' }, absent)).toBe(false);
  });

  it('summarises a saved record', () => {
    expect(savedSummary(marked)).toBe('In 09:41 · Out 18:12 · Late Login');
    expect(savedSummary({ marked: true, attendance_type: 'Leave' })).toBe('Leave');
    expect(savedSummary(fresh)).toBe('');
  });
});
