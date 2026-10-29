// client.js is a thin axios wrapper: a shared instance with a request
// interceptor (Bearer token attach) and a response interceptor (session-
// dead detection on 401s), plus ~90 exported call functions that are mostly
// `client.get/post/put/delete` one-liners. Rather than exhaustively testing
// every export (mechanically identical for a given "shape"), this covers:
//   - the interceptors themselves (token attach, 401 handling)
//   - every *distinct* call shape used across the file (see describe titles)
//   - the company-signup and registration-checklist functions in full
//   - the company prefix every API call is sent under
//   - representative auth / attendance / multipart / error-path coverage

const mockAxiosInstance = {
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  delete: jest.fn(),
  interceptors: {
    request: { use: jest.fn() },
    response: { use: jest.fn() },
  },
};

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    create: jest.fn(() => mockAxiosInstance),
  },
}));

jest.mock('../../utils/secureStorage', () => ({
  __esModule: true,
  secureGetItem: jest.fn(),
}));

const axios = require('axios');
const { secureGetItem } = require('../../utils/secureStorage');
const { API_BASE_URL } = require('../../config');
const client = require('../client');

describe('client.js axios instance setup', () => {
  it('creates the shared axios instance pointed at API_BASE_URL with a 10s timeout', () => {
    expect(axios.default.create).toHaveBeenCalledWith({ baseURL: API_BASE_URL, timeout: 10000 });
  });

  it('registers exactly one request interceptor and one response interceptor', () => {
    expect(mockAxiosInstance.interceptors.request.use).toHaveBeenCalledTimes(1);
    expect(mockAxiosInstance.interceptors.response.use).toHaveBeenCalledTimes(1);
  });
});

describe('request interceptor (Bearer token attach)', () => {
  const getRequestInterceptor = () => mockAxiosInstance.interceptors.request.use.mock.calls[0][0];

  afterEach(() => {
    secureGetItem.mockReset();
  });

  it('attaches Authorization: Bearer <token> when a token is stored', async () => {
    secureGetItem.mockResolvedValue('secret-token');
    const config = await getRequestInterceptor()({ headers: {} });
    expect(config.headers.Authorization).toBe('Bearer secret-token');
  });

  it('reads the token under the "token" key', async () => {
    secureGetItem.mockResolvedValue('abc');
    await getRequestInterceptor()({ headers: {} });
    expect(secureGetItem).toHaveBeenCalledWith('token');
  });

  it('leaves headers untouched when there is no stored token', async () => {
    secureGetItem.mockResolvedValue(null);
    const config = await getRequestInterceptor()({ headers: {} });
    expect(config.headers.Authorization).toBeUndefined();
  });

  it('returns the (possibly mutated) config either way', async () => {
    secureGetItem.mockResolvedValue(null);
    const original = { headers: {}, url: '/api/whatever' };
    const config = await getRequestInterceptor()(original);
    expect(config).toBe(original);
  });
});

describe('response interceptor (dead-session detection)', () => {
  const getResponseErrorHandler = () => mockAxiosInstance.interceptors.response.use.mock.calls[0][1];
  const getResponseSuccessHandler = () => mockAxiosInstance.interceptors.response.use.mock.calls[0][0];

  let handler;
  beforeEach(() => {
    handler = jest.fn();
    client.setUnauthorizedHandler(handler);
  });

  afterEach(() => {
    client.setUnauthorizedHandler(null);
  });

  it('passes successful responses through unchanged', () => {
    const response = { data: 'ok' };
    expect(getResponseSuccessHandler()(response)).toBe(response);
  });

  it('fires the unauthorized handler on a 401 from a session-critical path (/api/dashboard)', async () => {
    const error = { response: { status: 401 }, config: { url: '/api/dashboard' } };
    await expect(getResponseErrorHandler()(error)).rejects.toBe(error);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('fires the unauthorized handler on a 401 from /api/employee/portal too', async () => {
    const error = { response: { status: 401 }, config: { url: '/api/employee/portal' } };
    await expect(getResponseErrorHandler()(error)).rejects.toBe(error);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire the unauthorized handler on a 401 from an unrelated endpoint', async () => {
    const error = { response: { status: 401 }, config: { url: '/api/employee/tickets' } };
    await expect(getResponseErrorHandler()(error)).rejects.toBe(error);
    expect(handler).not.toHaveBeenCalled();
  });

  it('does not fire the handler for non-401 errors', async () => {
    const error = { response: { status: 500 }, config: { url: '/api/dashboard' } };
    await expect(getResponseErrorHandler()(error)).rejects.toBe(error);
    expect(handler).not.toHaveBeenCalled();
  });

  it('does not throw when there is no response at all (network error) and no handler registered', async () => {
    client.setUnauthorizedHandler(null);
    const error = new Error('Network Error');
    await expect(getResponseErrorHandler()(error)).rejects.toBe(error);
  });

  it('does not throw a 401 session-critical error when no handler has been registered', async () => {
    client.setUnauthorizedHandler(null);
    const error = { response: { status: 401 }, config: { url: '/api/dashboard' } };
    await expect(getResponseErrorHandler()(error)).rejects.toBe(error);
  });
});

describe('company signup', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
    mockAxiosInstance.get.mockReset();
  });

  it('startCompanySignup POSTs the raw payload to /api/create_org', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { application_id: 'app1' } });
    const payload = { company_name: 'Acme', admin_email: 'a@acme.com' };
    await client.startCompanySignup(payload);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/create_org', payload);
  });

  it('verifyCompanySignupOtp POSTs application_id/access_token/otp_code', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.verifyCompanySignupOtp('app1', 'tok1', '123456');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/create_org/verify_otp', {
      application_id: 'app1',
      access_token: 'tok1',
      otp_code: '123456',
    });
  });

  it('resendCompanySignupOtp POSTs application_id/access_token', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.resendCompanySignupOtp('app1', 'tok1');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/create_org/resend_otp', {
      application_id: 'app1',
      access_token: 'tok1',
    });
  });

  it('propagates a rejected promise (e.g. duplicate application / validation error) untouched', async () => {
    const apiError = { response: { status: 409, data: { error: 'duplicate' } } };
    mockAxiosInstance.post.mockRejectedValueOnce(apiError);
    await expect(client.startCompanySignup({})).rejects.toBe(apiError);
  });

  it('propagates a network failure (no response) from the OTP verify call', async () => {
    const networkError = new Error('Network Error');
    mockAxiosInstance.post.mockRejectedValueOnce(networkError);
    await expect(client.verifyCompanySignupOtp('app1', 'tok1', '000000')).rejects.toBe(networkError);
  });
});

describe('auth: admin + employee login/logout', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
  });

  it('adminLogin POSTs username/password to /api/login', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { token: 't' } });
    await client.adminLogin('admin1', 'pw');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/login', {
      username: 'admin1',
      password: 'pw',
    });
  });

  it('verifyAdminLogin POSTs challenge_id/otp_code to /api/login/verify', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { token: 't' } });
    await client.verifyAdminLogin('chal-1', '123456');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/login/verify', {
      challenge_id: 'chal-1',
      otp_code: '123456',
    });
  });

  it('adminLogout POSTs to /api/logout with no body', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.adminLogout();
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/logout');
  });

  it('employeeLogin POSTs employee_id/password to /api/employee/login', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { token: 't' } });
    await client.employeeLogin('EMP1', 'pw');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/login', {
      employee_id: 'EMP1',
      password: 'pw',
    });
  });

  it('employeeSignup applies its default role/department/email when omitted', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.employeeSignup('EMP1', 'Alice', 'pw');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/signup', {
      employee_id: 'EMP1',
      name: 'Alice',
      password: 'pw',
      email: '',
      role: 'Employee',
      department: 'Engineering',
    });
  });

  it('employeeSignup forwards explicit role/department/email when given', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.employeeSignup('EMP1', 'Alice', 'pw', 'a@x.com', 'Manager', 'Sales');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/signup', {
      employee_id: 'EMP1',
      name: 'Alice',
      password: 'pw',
      email: 'a@x.com',
      role: 'Manager',
      department: 'Sales',
    });
  });

  it('changePassword POSTs current/new password', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.changePassword('old', 'new');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/change-password', {
      current_password: 'old',
      new_password: 'new',
    });
  });

  it('employeeLogout POSTs to /api/employee/logout with no body', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.employeeLogout();
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/logout');
  });

  it('propagates a rejected login (invalid credentials, 401) untouched', async () => {
    const apiError = { response: { status: 401, data: { error: 'Invalid credentials' } } };
    mockAxiosInstance.post.mockRejectedValueOnce(apiError);
    await expect(client.adminLogin('admin1', 'wrong')).rejects.toBe(apiError);
  });

  it('propagates a network failure from employeeLogin untouched', async () => {
    const networkError = new Error('Network Error');
    mockAxiosInstance.post.mockRejectedValueOnce(networkError);
    await expect(client.employeeLogin('EMP1', 'pw')).rejects.toBe(networkError);
  });
});

describe('attendance check-in / check-out', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
  });

  it('employeeCheckin POSTs lat/lon to /api/employee/checkin', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.employeeCheckin(12.34, 56.78);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/checkin', {
      lat: 12.34,
      lon: 56.78,
    });
  });

  it('attendanceCheckin (QR/face check-in+out) POSTs FormData with multipart headers and a 30s timeout', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    const formData = new FormData();
    formData.append('image', 'face-blob');
    formData.append('emp_id', 'EMP1');
    await client.attendanceCheckin(formData);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith(
      '/api/employee/qr-face-checkin',
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 }
    );
  });

  it('qrFaceCheckin hits the same endpoint as attendanceCheckin', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    const formData = new FormData();
    await client.qrFaceCheckin(formData);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith(
      '/api/employee/qr-face-checkin',
      formData,
      expect.objectContaining({ timeout: 30000 })
    );
  });

  it('syncOfflinePunches POSTs the queued punches array', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    const punches = [{ id: '1', lat: 1, lon: 2 }];
    await client.syncOfflinePunches(punches);
    // device_id (security-hardening's punch integrity) is null when the caller has none
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/sync_punches', { punches, device_id: null });
  });

  it('syncOfflinePunches sends the device id when given', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    const punches = [{ id: '1', lat: 1, lon: 2 }];
    await client.syncOfflinePunches(punches, 'dev-123');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/sync_punches', { punches, device_id: 'dev-123' });
  });

  it('propagates a network failure from employeeCheckin (offline) untouched', async () => {
    const networkError = new Error('Network Error');
    mockAxiosInstance.post.mockRejectedValueOnce(networkError);
    await expect(client.employeeCheckin(1, 2)).rejects.toBe(networkError);
  });

  it('propagates a non-2xx failure from attendanceCheckin (e.g. face mismatch) untouched', async () => {
    const apiError = { response: { status: 400, data: { error: 'Face not recognized' } } };
    mockAxiosInstance.post.mockRejectedValueOnce(apiError);
    await expect(client.attendanceCheckin(new FormData())).rejects.toBe(apiError);
  });
});

describe('GET with no params', () => {
  beforeEach(() => {
    mockAxiosInstance.get.mockReset();
  });

  it('fetchDashboard GETs /api/dashboard', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: {} });
    await client.fetchDashboard();
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/dashboard');
  });

  it('fetchEmployees GETs /api/employees', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: [] });
    await client.fetchEmployees();
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/employees');
  });

  it('fetchEmployeePortal GETs /api/employee/portal', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: {} });
    await client.fetchEmployeePortal();
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/employee/portal');
  });
});

describe('GET with query params', () => {
  beforeEach(() => {
    mockAxiosInstance.get.mockReset();
  });

  it('fetchAttendanceEmployees GETs with { date } as params', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: [] });
    await client.fetchAttendanceEmployees('2026-09-01');
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/bulk_mark_attendance', {
      params: { date: '2026-09-01' },
    });
  });

  it('fetchMonthlyReport GETs with { year, month } as params', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: [] });
    await client.fetchMonthlyReport(2026, 9);
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/monthly_report', {
      params: { year: 2026, month: 9 },
    });
  });

  it('fetchEmployeeSalary GETs with { year, month } as params', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: {} });
    await client.fetchEmployeeSalary(2026, 9);
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/employee/salary', {
      params: { year: 2026, month: 9 },
    });
  });
});

describe('GET with a conditional params object', () => {
  beforeEach(() => {
    mockAxiosInstance.get.mockReset();
  });

  it('fetchDocuments passes { params: { emp_id } } when an empId is given', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: [] });
    await client.fetchDocuments('EMP1');
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/documents', { params: { emp_id: 'EMP1' } });
  });

  it('fetchDocuments passes undefined config when no empId is given (fetch-all)', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: [] });
    await client.fetchDocuments();
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/documents', undefined);
  });

  it('fetchMyOnboarding passes { params: { ob_id } } when an obId is given', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: {} });
    await client.fetchMyOnboarding('OB1');
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/employee/onboarding', {
      params: { ob_id: 'OB1' },
    });
  });

  it('fetchMyOnboarding passes an empty params object when no obId is given', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: {} });
    await client.fetchMyOnboarding();
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/employee/onboarding', { params: {} });
  });

  it('getWebSessionLink sends { target } only when a target is given', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.getWebSessionLink('/settings/seats');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/mobile/web_session_link', {
      target: '/settings/seats',
    });
  });

  it('getWebSessionLink sends {} when no target is given (server default)', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.getWebSessionLink();
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/mobile/web_session_link', {});
  });
});

describe('POST with positional-args body', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
    mockAxiosInstance.put.mockReset();
    mockAxiosInstance.delete.mockReset();
  });

  it('addHoliday POSTs { date, name }', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.addHoliday('2026-01-26', 'Republic Day');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/holidays', {
      date: '2026-01-26',
      name: 'Republic Day',
    });
  });

  it('createShift POSTs { name, start_time, half_time, end_time }', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.createShift('Morning', '09:00', '13:00', '18:00');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/shifts', {
      name: 'Morning',
      start_time: '09:00',
      half_time: '13:00',
      end_time: '18:00',
    });
  });

  it('leaveAction POSTs { action } to the leave request id path', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.leaveAction(42, 'approve');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/leave_requests/42/action', {
      action: 'approve',
    });
  });

  it('overtimeAction defaults notes to an empty string when omitted', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.overtimeAction(7, 'reject');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/overtime/7/action', {
      action: 'reject',
      notes: '',
    });
  });

  it('sendEmailBlast renames camelCase args to the snake_case body the backend expects', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.sendEmailBlast('department', 'Engineering', 'Subject', 'Body text');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/admin/email-blast', {
      target_type: 'department',
      target_value: 'Engineering',
      subject: 'Subject',
      body: 'Body text',
    });
  });

  it('submitPerformanceReview builds the review upsert body with its default status', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.submitPerformanceReview('EMP1', 'Q1', 2026, 'Great work');
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/performance/review', {
      employee_id: 'EMP1',
      quarter: 'Q1',
      year: 2026,
      reviewer_feedback: 'Great work',
      status: 'Draft',
    });
  });

  it('saveSalaryRules maps all five positional args to their snake_case fields', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.saveSalaryRules(5, 10, 15, true, false);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/settings/salary_rules', {
      late_deduction_pct: 5,
      half_day_deduction_pct: 10,
      grace_minutes: 15,
      holiday_pay: true,
      leave_pay: false,
    });
  });

  it('addEmployee uppercases employee_id and makes up nothing the admin left blank', async () => {
    // No default password (it was a guessable 'welcome123') and no invented
    // department -- the screen requires the password, the server checks it.
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.addEmployee({ employee_id: 'emp2', name: 'Bob', email: 'b@x.com' });
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/signup', {
      employee_id: 'EMP2',
      name: 'Bob',
      password: undefined,
      email: 'b@x.com',
      role: 'Employee',
      department: '',
      phone: '',
      date_of_joining: '',
    });
  });

  it('addEmployee falls back to the "emp_id" field and honors explicit overrides', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.addEmployee({
      emp_id: 'emp3',
      name: 'Carol',
      password: 'custompw',
      role: 'Manager',
      department: 'Sales',
      phone: '9123456780',
      date_of_joining: '2026-10-01',
    });
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/employee/signup', {
      employee_id: 'EMP3',
      name: 'Carol',
      password: 'custompw',
      email: '',
      role: 'Manager',
      department: 'Sales',
      phone: '9123456780',
      date_of_joining: '2026-10-01',
    });
  });

  it('editEmployee PUTs only the 4 whitelisted fields, dropping anything else on the object', async () => {
    mockAxiosInstance.put.mockResolvedValue({ data: {} });
    await client.editEmployee('EMP1', {
      name: 'Alice',
      email: 'a@x.com',
      role: 'Manager',
      date_of_joining: '2020-01-01',
      salary: 999999, // should NOT be forwarded
    });
    expect(mockAxiosInstance.put).toHaveBeenCalledWith('/api/employees/EMP1', {
      name: 'Alice',
      email: 'a@x.com',
      role: 'Manager',
      date_of_joining: '2020-01-01',
    });
  });

  it('deleteHoliday DELETEs the holiday id path', async () => {
    mockAxiosInstance.delete.mockResolvedValue({ data: {} });
    await client.deleteHoliday(9);
    expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/api/holidays/9');
  });

  it('deleteEmployee DELETEs the employee id path', async () => {
    mockAxiosInstance.delete.mockResolvedValue({ data: {} });
    await client.deleteEmployee('EMP1');
    expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/api/employees/EMP1');
  });
});

describe('multipart uploads (raw FormData passthrough)', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
  });

  it.each([
    ['uploadEmployeePhoto', '/api/employee/photo'],
    ['uploadDocument', '/api/employee/documents/upload'],
    ['uploadDocumentForEmployee', '/api/documents/upload'],
  ])('%s POSTs the given FormData to %s with multipart headers and a 30s timeout', async (fnName, url) => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    const formData = new FormData();
    formData.append('file', 'blob');
    await client[fnName](formData);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 30000,
    });
  });

  it('propagates a rejected upload (e.g. file too large) untouched', async () => {
    const apiError = { response: { status: 413, data: { error: 'File too large' } } };
    mockAxiosInstance.post.mockRejectedValueOnce(apiError);
    await expect(client.uploadDocument(new FormData())).rejects.toBe(apiError);
  });
});

describe('multipart upload built internally from scalar args', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
  });

  it('completeMyOnboardingTask builds its own FormData with ob_id and employee_note', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.completeMyOnboardingTask('TASK1', 'OB1', 'All done');

    expect(mockAxiosInstance.post).toHaveBeenCalledTimes(1);
    const [url, body, config] = mockAxiosInstance.post.mock.calls[0];
    expect(url).toBe('/api/employee/onboarding/task/TASK1/done');
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('ob_id')).toBe('OB1');
    expect(body.get('employee_note')).toBe('All done');
    expect(config).toEqual({ headers: { 'Content-Type': 'multipart/form-data' } });
  });

  it('completeMyOnboardingTask defaults employee_note to an empty string', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    await client.completeMyOnboardingTask('TASK1', 'OB1');
    const [, body] = mockAxiosInstance.post.mock.calls[0];
    expect(body.get('employee_note')).toBe('');
  });
});

describe('registration checklist', () => {
  beforeEach(() => {
    mockAxiosInstance.post.mockReset();
    mockAxiosInstance.get.mockReset();
  });

  it('getRegistrationChecklist GETs /api/admin/registration', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: { ok: true, steps: [] } });
    await client.getRegistrationChecklist();
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/admin/registration');
  });

  it.each([
    ['saveCompanyDetails', '/api/admin/registration/company'],
    ['uploadRegistrationDocuments', '/api/admin/registration/documents'],
  ])('%s POSTs the FormData to %s as multipart with a 30s timeout', async (fn, url) => {
    mockAxiosInstance.post.mockResolvedValue({ data: {} });
    const formData = new FormData();
    await client[fn](formData);
    expect(mockAxiosInstance.post).toHaveBeenCalledWith(
      url, formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 }
    );
  });
});

describe('company prefix', () => {
  const getRequestInterceptor = () => mockAxiosInstance.interceptors.request.use.mock.calls[0][0];

  afterEach(() => {
    client.setCompanySlug('');
    secureGetItem.mockReset();
  });

  it('leaves URLs alone for the default company', () => {
    expect(client.withCompany('/api/dashboard')).toBe('/api/dashboard');
  });

  it('sends API calls under the signed-in company', async () => {
    client.setCompanySlug('acme');
    expect(client.getCompanySlug()).toBe('acme');
    expect(client.withCompany('/api/dashboard')).toBe('/acme/api/dashboard');
    expect(client.withCompany('/acme/api/dashboard')).toBe('/acme/api/dashboard');
    secureGetItem.mockResolvedValue(null);
    const config = await getRequestInterceptor()({ url: '/api/login', headers: {} });
    expect(config.url).toBe('/acme/api/login');
  });

  it('never prefixes signup, which belongs to no company yet', () => {
    client.setCompanySlug('acme');
    expect(client.withCompany('/api/create_org')).toBe('/api/create_org');
    expect(client.withCompany('/api/create_org/verify_otp')).toBe('/api/create_org/verify_otp');
  });

  it('puts employee photos under the company too', () => {
    client.setCompanySlug('acme');
    expect(client.getPhotoUrl('EMP1')).toBe(`${API_BASE_URL}/acme/dataset/EMP1.jpg`);
  });

  it.each([
    ['acme', 'acme'],
    ['  Acme-Corp ', 'acme-corp'],
    ['hrzest.com/acme', 'acme'],
    ['https://www.hrzest.com/acme/login', 'acme'],
    ['https://203.0.113.10/acme/login', 'acme'],
    ['192.168.1.9:5000/acme', 'acme'],
    ['localhost:5000/acme', 'acme'],
    ['www.hrzest.com', ''],
    ['', ''],
  ])('parseCompanySlug(%j) is %j', (typed, slug) => {
    expect(client.parseCompanySlug(typed)).toBe(slug);
  });
});

describe('getPhotoUrl (pure string builder, no axios call)', () => {
  beforeEach(() => {
    mockAxiosInstance.get.mockReset();
    mockAxiosInstance.post.mockReset();
  });

  it('builds a dataset URL from the API base and employee id', () => {
    expect(client.getPhotoUrl('EMP1')).toBe(`${API_BASE_URL}/dataset/EMP1.jpg`);
    expect(mockAxiosInstance.get).not.toHaveBeenCalled();
    expect(mockAxiosInstance.post).not.toHaveBeenCalled();
  });
});
