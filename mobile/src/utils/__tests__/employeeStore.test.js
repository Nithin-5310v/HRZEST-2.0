// employeeStore.js now only removes the staff-list copy older app versions
// kept on the phone (it showed deleted employees and miscounted staff).
const mockRemove = jest.fn(() => Promise.resolve());
jest.mock('../secureStorage', () => ({
  secureRemoveItem: (...args) => mockRemove(...args),
}));

import { clearLocalEmployees } from '../employeeStore';

describe('employeeStore', () => {
  beforeEach(() => mockRemove.mockClear());

  it('removes the old cached staff list', async () => {
    await clearLocalEmployees();
    expect(mockRemove).toHaveBeenCalledWith('@custom_created_employees_v1');
  });

  it('never throws, even if storage fails', async () => {
    mockRemove.mockImplementationOnce(() => Promise.reject(new Error('boom')));
    await expect(clearLocalEmployees()).resolves.toBeUndefined();
  });
});
