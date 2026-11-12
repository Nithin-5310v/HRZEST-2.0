import { todayState, sortForToday } from '../TodayAttendance';

describe('TodayAttendance', () => {
  it('labels each employee by what happened today', () => {
    expect(todayState({ login_time: '09:05' }).label).toBe('Checked in');
    expect(todayState({ login_time: '09:45', status: 'Late Login' }).label).toBe('In · Late');
    expect(todayState({ login_time: '09:05', logout_time: '18:01' }).label).toBe('Checked out');
    expect(todayState({ attendance_type: 'Leave' }).label).toBe('Leave');
    expect(todayState({ attendance_type: 'Absent' }).tone).toBe('bad');
    expect(todayState({}).label).toBe('Not checked in');
  });

  it('lists check-ins first by time, then marked days, then nobody yet', () => {
    const sorted = sortForToday([
      { employee_id: 'N', name: 'Nobody' },
      { employee_id: 'L', name: 'Leave', attendance_type: 'Leave' },
      { employee_id: 'B', name: 'Bea', login_time: '09:30:00' },
      { employee_id: 'A', name: 'Al', login_time: '08:55' },
    ]);
    expect(sorted.map((e) => e.employee_id)).toEqual(['A', 'B', 'L', 'N']);
  });
});
