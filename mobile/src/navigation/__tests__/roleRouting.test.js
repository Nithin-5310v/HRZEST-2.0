import { getNavigatorKey, showsMeTab } from '../roleRouting';

describe('getNavigatorKey', () => {
  it('returns null when there is no user (logged out)', () => {
    expect(getNavigatorKey(null)).toBe(null);
    expect(getNavigatorKey(undefined)).toBe(null);
  });

  it('routes a plain admin to the admin navigator', () => {
    expect(getNavigatorKey({ role: 'admin', adminRole: 'admin' })).toBe('admin');
  });

  it('routes an HR-role admin to the admin navigator (unchanged behavior)', () => {
    expect(getNavigatorKey({ role: 'admin', adminRole: 'hr' })).toBe('admin');
  });

  it('routes a manager-role admin to the manager navigator', () => {
    expect(getNavigatorKey({ role: 'admin', adminRole: 'manager' })).toBe('manager');
  });

  it('routes an employee to the employee navigator', () => {
    expect(getNavigatorKey({ role: 'employee' })).toBe('employee');
  });

  it('falls back to admin when adminRole is missing entirely (defensive)', () => {
    expect(getNavigatorKey({ role: 'admin' })).toBe('admin');
  });

  it('returns null for an unrecognized role rather than guessing', () => {
    expect(getNavigatorKey({ role: 'something_new' })).toBe(null);
  });
});

describe('showsMeTab', () => {
  it('shows for the owner admin once their own employee profile is linked', () => {
    expect(showsMeTab({ role: 'admin', adminRole: 'admin', linkedEmployeeId: 'EMP-007' })).toBe(true);
  });

  it('shows for an older saved admin session without adminRole', () => {
    expect(showsMeTab({ role: 'admin', linkedEmployeeId: 'EMP-007' })).toBe(true);
  });

  it('stays hidden until the profile exists', () => {
    expect(showsMeTab({ role: 'admin', adminRole: 'admin' })).toBe(false);
    expect(showsMeTab({ role: 'admin', adminRole: 'admin', linkedEmployeeId: null })).toBe(false);
  });

  it('never shows for HR or manager logins, which already are employees', () => {
    expect(showsMeTab({ role: 'admin', adminRole: 'hr', linkedEmployeeId: 'EMP-007' })).toBe(false);
    expect(showsMeTab({ role: 'admin', adminRole: 'manager', linkedEmployeeId: 'EMP-007' })).toBe(false);
  });

  it('never shows for an employee session or no session', () => {
    expect(showsMeTab({ role: 'employee', employeeId: 'EMP-007' })).toBe(false);
    expect(showsMeTab(null)).toBe(false);
  });
});
