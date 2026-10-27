import axios from 'axios';
import { API_BASE_URL } from '../config';
import { secureGetItem } from '../utils/secureStorage';

const client = axios.create({ baseURL: API_BASE_URL, timeout: 10000 });

client.interceptors.request.use(async (config) => {
  // A release build whose API URL is not https (EXPO_PUBLIC_API_BASE_URL
  // missing -> config.js falls back to a plain-http dev host) would send the
  // Bearer token in clear text. Refuse to make the request at all rather than
  // leak it; development builds (__DEV__) are unaffected.
  if (!__DEV__ && !/^https:\/\//i.test(String(config.baseURL || API_BASE_URL))) {
    throw new Error('Refusing to call an insecure (non-https) API URL in a release build');
  }
  const token = await secureGetItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// AuthContext registers a handler here so a genuinely dead session (the
// Bearer token expired/was revoked) clears storage and bounces the user
// back to login instead of leaving them stuck.
//
// Deliberately NOT wired to every 401 in the app: several endpoints 401 for
// reasons that have nothing to do with the session being dead -- wrong
// token type for that route, a feature endpoint that still expects a
// session cookie, etc. Auto-logging out on any of those would silently
// kill a perfectly valid session over an unrelated background call, which
// is exactly why we don't do that here. Instead, only 401s from the calls
// that establish "is my session even alive" (the dashboards' own initial
// load) are treated as fatal.
const SESSION_CRITICAL_PATHS = ['/api/dashboard', '/api/employee/portal'];

let unauthorizedHandler = null;
export const setUnauthorizedHandler = (handler) => {
  unauthorizedHandler = handler;
};

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error?.response?.status;
    if (status === 401) {
      const url = error?.config?.url || '';
      const isSessionCritical = SESSION_CRITICAL_PATHS.some((p) => url.includes(p));
      if (isSessionCritical && unauthorizedHandler) {
        unauthorizedHandler();
      }
    }
    return Promise.reject(error);
  }
);

// ── Admin API ──────────────────────────────────────────────────────
// Step 1: password. When the server requires login MFA this returns
// { ok, mfa_required: true, challenge_id } instead of a token, and the
// one-time code is emailed to the account.
export const adminLogin = (username, password) =>
  client.post('/api/login', { username, password });

// Step 2: exchange the challenge_id + emailed code for the Bearer token.
export const verifyAdminLogin = (challenge_id, otp_code) =>
  client.post('/api/login/verify', { challenge_id, otp_code });

export const adminLogout = () => client.post('/api/logout');

export const fetchDashboard = () => client.get('/api/dashboard');

export const fetchEmployees = () => client.get('/api/employees');

// ── Admin: Mark Attendance ──────────────────────────────────────────
export const fetchAttendanceEmployees = (date) =>
  client.get('/api/bulk_mark_attendance', { params: { date } });

export const markAttendance = (date, records) =>
  client.post('/api/bulk_mark_attendance', { date, records });

export const fetchHolidays = () => client.get('/api/holidays');

export const addHoliday = (date, name) => client.post('/api/holidays', { date, name });

export const deleteHoliday = (holidayId) => client.delete(`/api/holidays/${holidayId}`);

export const fetchShifts = () => client.get('/api/shifts');

export const createShift = (name, start_time, half_time, end_time) =>
  client.post('/api/shifts', { name, start_time, half_time, end_time });

export const deleteShift = (sid) => client.delete(`/api/shifts/${sid}`);

export const assignShift = (emp_id, shift_id) =>
  client.post('/api/shifts/assign', { emp_id, shift_id });

export const fetchMonthlyReport = (year, month) =>
  client.get('/api/monthly_report', { params: { year, month } });

export const fetchSalaryReport = (year, month) =>
  client.get('/api/salary_report', { params: { year, month } });

export const fetchLeaveRequests = () => client.get('/api/leave_requests');

export const leaveAction = (lid, action) =>
  client.post(`/api/leave_requests/${lid}/action`, { action });

export const fetchResignations = () => client.get('/api/resignation_requests');

export const resignationAction = (rid, action) =>
  client.post(`/api/resignation_requests/${rid}/action`, { action });

export const fetchOvertime = () => client.get('/api/overtime');

export const overtimeAction = (oid, action, notes = '') =>
  client.post(`/api/overtime/${oid}/action`, { action, notes });

export const fetchCompOff = () => client.get('/api/compoff');

export const fetchPerformance = () => client.get('/api/performance');

export const fetchOnboarding = () => client.get('/api/onboarding');

export const fetchDepartments = () => client.get('/api/departments');

// ── Employee API ───────────────────────────────────────────────────
export const employeeLogin = (employee_id, password) =>
  client.post('/api/employee/login', { employee_id, password });

export const employeeSignup = (employee_id, name, password, email = '', role = 'Employee', department = 'Engineering') =>
  client.post('/api/employee/signup', {
    employee_id,
    name,
    password,
    email,
    role,
    department,
  });

export const changePassword = (current_password, new_password) =>
  client.post('/api/employee/change-password', { current_password, new_password });

// ── Company Signup (gated: OTP verification + KYC document upload + ─
// platform-admin review, before a tenant is ever provisioned) ────────
export const startCompanySignup = (payload) => client.post('/api/create_org', payload);

export const verifyCompanySignupOtp = (application_id, access_token, otp_code) =>
  client.post('/api/create_org/verify_otp', { application_id, access_token, otp_code });

export const resendCompanySignupOtp = (application_id, access_token) =>
  client.post('/api/create_org/resend_otp', { application_id, access_token });

// The company profile, after the email code (multipart: the logo is optional).
export const submitCompanyProfile = (formData) =>
  client.post('/api/create_org/profile', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 });

export const uploadCompanyDocuments = (formData) =>
  client.post('/api/create_org/upload_documents', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 });

export const getCompanySignupStatus = (application_id, access_token) =>
  client.get(`/api/create_org/status/${application_id}`, { params: { access_token } });

export const uploadEmployeePhoto = (formData) =>
  client.post('/api/employee/photo', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 });

export const qrFaceCheckin = (formData) =>
  client.post('/api/employee/qr-face-checkin', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 });

export const getAuthConfig = () =>
  client.get('/api/employee/auth-config');

export const getMobileBiometricNonce = () =>
  client.post('/api/employee/mobile-biometric-nonce');

export const attestMobileBiometric = (nonce) =>
  client.post('/api/employee/mobile-biometric-attest', { nonce });

export const attendanceCheckin = (formData) =>
  client.post('/api/employee/qr-face-checkin', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 30000 });

export const getPhotoUrl = (empId) => `${API_BASE_URL}/dataset/${empId}.jpg`;

export const employeeLogout = () => client.post('/api/employee/logout');

export const fetchEmployeePortal = () => client.get('/api/employee/portal');

export const employeeCheckin = (lat, lon, extra = {}) =>
  client.post('/api/employee/checkin', { lat, lon, ...extra });

// Single-use liveness challenge for a face check-in (server picks the action).
export const getAttendanceChallenge = (employeeId) =>
  client.post('/api/attendance/challenge', { employee_id: employeeId });

export const syncOfflinePunches = (punches, device_id = null) =>
  client.post('/api/employee/sync_punches', { punches, device_id });

export const submitLeaveRequest = (leave_date, reason) =>
  client.post('/api/employee/leave_request', { leave_date, reason });

export const submitResignation = (last_working_day, reason) =>
  client.post('/api/employee/resign', { last_working_day, reason });

export const fetchEmployeeTickets = () => client.get('/api/employee/tickets');

export const raiseTicket = (category, subject, description, priority) =>
  client.post('/api/employee/raise_ticket', { category, subject, description, priority });

// ── Admin: Tickets ─────────────────────────────────────────────────
export const fetchAllTickets = () => client.get('/api/tickets');

export const ticketAction = (tid, status, admin_response) =>
  client.post(`/api/tickets/${tid}/action`, { status, admin_response });

export const fetchEmployeeSalary = (year, month) =>
  client.get('/api/employee/salary', { params: { year, month } });

export const fetchEmployeeAttendance = (year, month) =>
  client.get('/api/employee/attendance', { params: { year, month } });

export const fetchEmployeeLeaves = () => client.get('/api/employee/leaves');

export const cancelLeaveRequest = (lid) =>
  client.post(`/api/employee/cancel_leave/${lid}`);

export const requestOvertime = (date, reason) =>
  client.post('/api/employee/request_overtime', { date, reason });

export const fetchMyOvertime = () => client.get('/api/employee/my_overtime');

// Both return { ok, image_base64 } rather than a raw image stream -- same
// reason as fetchSalaryReportExport above (axios in React Native has no
// reliable blob/arraybuffer download path). Employee gets their own card
// only; the admin/HR variant is HR-scoped server-side to that HR's
// assigned employees (blueprints/employees.py's api_employee_id_card()).
export const fetchMyIdCard = () => client.get('/api/employee/my_id_card');

export const fetchEmployeeIdCard = (empId) => client.get(`/api/employees/${empId}/id_card`);

export const fetchEmployeeHolidays = () => client.get('/api/employee/holidays');

export const fetchEmployeeProfile = () => client.get('/api/employee/profile');

export const updateMyProfile = (fields) => client.post('/api/employee/profile', fields);

export const updateMyBankDetails = (fields) => client.post('/api/employee/bank_details', fields);

export const updateNotificationPreferences = (emailAlertsEnabled) =>
  client.post('/api/employee/notification_preferences', { email_alerts_enabled: emailAlertsEnabled });

export const fetchMyExperience = () => client.get('/api/employee/experience');

export const addMyExperience = (entry) => client.post('/api/employee/experience', entry);

export const deleteMyExperience = (entryId) => client.delete(`/api/employee/experience/${entryId}`);

export const fetchMyEducation = () => client.get('/api/employee/education');

export const addMyEducation = (entry) => client.post('/api/employee/education', entry);

export const deleteMyEducation = (entryId) => client.delete(`/api/employee/education/${entryId}`);

export const fetchMyOnboarding = (obId) =>
  client.get('/api/employee/onboarding', { params: obId ? { ob_id: obId } : {} });

export const completeMyOnboardingTask = (taskId, obId, employeeNote = '') => {
  const form = new FormData();
  form.append('ob_id', obId);
  form.append('employee_note', employeeNote);
  return client.post(`/api/employee/onboarding/task/${taskId}/done`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const fetchMyPerformance = () => client.get('/api/employee/performance');

export const submitMyPerformanceComment = (reviewId, comment) =>
  client.post('/api/employee/performance/comment', { review_id: reviewId, comment });

// ── Notifications ──────────────────────────────────────────────────
export const fetchNotifications = () => client.get('/api/notifications');
export const markNotificationsRead = () => client.post('/api/notifications/mark_read');
export const fetchEmployeeNotifications = () => client.get('/api/employee/notifications');
export const markEmployeeNotificationsRead = () => client.post('/api/employee/notifications/mark_read');

// ── Admin: Staff & Settings & AI ──────────────────────────────────
export const addEmployee = (employeeData) =>
  client.post('/api/employee/signup', {
    employee_id: (employeeData.employee_id || employeeData.emp_id || '').toUpperCase(),
    name: employeeData.name,
    password: employeeData.password || 'welcome123',
    email: employeeData.email,
    role: employeeData.role || 'Software Engineer',
    department: employeeData.department || 'Engineering',
  });
// forgotPassword, resetPassword, addHoliday, deleteHoliday stay removed --
// their routes (/api/forgot-password, /api/reset-password,
// /api/holidays/add, /api/holidays/<id>/delete) don't exist anywhere in
// the backend, Bearer or session-based, and none had a mobile caller left
// after removing the broken UI that used them.
//
// editEmployee/deleteEmployee/getEmployeeDetail DO have real Bearer-token
// backends -- blueprints/employees.py's api_employee_detail (GET),
// api_edit_employee (PUT), api_delete_employee (DELETE), all at
// /api/employees/<emp_id> (RESTful method dispatch on one path, not a
// /edit or /delete suffix, which is why an earlier pass here concluded
// they didn't exist).
export const getEmployeeDetail = (empId) => client.get(`/api/employees/${empId}`);

export const editEmployee = (empId, employeeData) =>
  client.put(`/api/employees/${empId}`, {
    name: employeeData.name,
    email: employeeData.email,
    role: employeeData.role,
    date_of_joining: employeeData.date_of_joining,
  });

export const deleteEmployee = (empId) => client.delete(`/api/employees/${empId}`);

// Real manager_id-based reporting hierarchy -- Bearer twin of
// blueprints/admin_views.py's session-only /api/org_chart_data.
export const fetchOrgChart = () => client.get('/api/org_chart');

// Bearer twin of blueprints/email_blast.py's api_email_blast -- targetType
// is 'all' | 'department' | 'individual', targetValue is the department
// name or employee_id (ignored for 'all').
export const sendEmailBlast = (targetType, targetValue, subject, body) =>
  client.post('/api/admin/email-blast', { target_type: targetType, target_value: targetValue, subject, body });
export const fetchAiHelpdeskResponse = (query) => client.post('/api/ai/hr-helpdesk', { query });

export const evaluateInterview = (candidateName, position, notes) =>
  client.post('/api/ai/evaluate-interview', { candidate_name: candidateName, position, notes });

export const fetchAttritionAnalytics = () => client.get('/api/ai/attrition-analytics');
// compoffAction/api_compoff_action removed together -- the backend route
// referenced a compoff_balances (plural) table that is never created
// anywhere in this codebase's schema, so it silently 500'd/no-op'd on
// every call, and there is no real "approve a comp-off request" concept
// to wire up anyway (comp-off is credited automatically from approved
// overtime -- see blueprints/leave.py's overtime_action()). See
// api_compoff()'s docstring for the read-side fix.
export const updateSettings = (settingsData) => client.post('/api/settings/update', settingsData);

// ── Additional Parity APIs ──────────────────────────────────────────
export const sendPayslipEmail = (empId, year, month) =>
  client.post('/api/send_salary_email', { emp_id: empId, year, month });

export const fetchPayrollStatus = (year, month) =>
  client.get('/api/payroll/status', { params: { year, month } });

export const lockPayroll = (year, month) => client.post('/api/payroll/lock', { year, month });

export const unlockPayroll = (year, month) => client.post('/api/payroll/unlock', { year, month });

// Returns the .xlsx as base64 JSON rather than a binary stream -- axios in
// React Native has no reliable blob/arraybuffer download path, but a plain
// JSON response decodes cleanly with expo-file-system's base64 file write
// (see src/utils/fileShare.js's shareBase64File).
export const fetchSalaryReportExport = (year, month) =>
  client.get('/api/payroll/salary_report_export', { params: { year, month } });

// Field names match what blueprints/performance.py's api_submit_performance_review()
// (and the web's performance_save_review()) actually store -- quarter/year
// upsert with reviewer feedback, not the rating/comments/hike/bonus shape
// this used to send (which had no matching backend route or table columns
// at all).
export const submitPerformanceReview = (employeeId, quarter, year, reviewerFeedback, status = 'Draft') =>
  client.post('/api/performance/review', {
    employee_id: employeeId, quarter, year,
    reviewer_feedback: reviewerFeedback, status,
  });

// createOnboardingTask (template creation) stays removed -- that's a
// bigger admin-config feature (multi-task templates), not the per-employee
// task-completion flow below, and still has no Bearer or session route.
//
// fetchOnboardingTasks/updateOnboardingTaskStatus DO have real backends now
// -- blueprints/onboarding.py's api_onboarding_tasks() (GET) and
// api_onboarding_task_update() (POST), Bearer twins of the session-only
// onboarding_detail()/onboarding_admin_task_update().
export const fetchOnboardingTasks = (obId) => client.get(`/api/onboarding/${obId}/tasks`);

export const updateOnboardingTaskStatus = (taskId, status, adminNotes = '') =>
  client.post(`/api/onboarding/task/${taskId}/update`, { status, admin_notes: adminNotes });

// blueprints/documents.py's Bearer twins of the session-only document
// routes -- both admin (manage any employee's documents) and the
// employee's own upload/list/delete of their own documents.
export const uploadDocument = (formData) =>
  client.post('/api/employee/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30000,
  });

export const fetchEmployeeDocuments = () => client.get('/api/employee/documents');

export const deleteMyDocument = (docId) => client.delete(`/api/employee/documents/${docId}`);

// ── Documents (admin) ──────────────────────────────────────────────
export const fetchDocuments = (empId) =>
  client.get('/api/documents', empId ? { params: { emp_id: empId } } : undefined);

export const uploadDocumentForEmployee = (formData) =>
  client.post('/api/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30000,
  });

export const deleteDocument = (docId) => client.delete(`/api/documents/${docId}`);

export const broadcastNotification = (title, message, audience = 'all') =>
  client.post('/api/notifications/broadcast', { title, message, audience });

export const addDepartment = (name, code = '') =>
  client.post('/api/departments/add', { name, code });

export const deleteDepartment = (deptId) =>
  client.post(`/api/departments/${deptId}/delete`);

// ── Seats & Billing ───────────────────────────────────────────────
// Employee-count-vs-plan-limit + monthly auto-debit status -- same
// underlying data as the web app's Seats & Billing page
// (templates/seat_checkout.html), served here as JSON.
export const fetchBillingStatus = () => client.get('/api/billing_status');

// Buying seats / enabling auto-debit needs Razorpay Checkout, which this
// app doesn't embed natively -- instead we bridge the current Bearer
// session into a one-time web session-cookie login and open that URL in
// an in-app WebView (see SeatsBillingScreen). The link is single-use and
// expires in 5 minutes. `target` must match one of the paths in
// blueprints/core.py's _BRIDGE_TARGET_ALLOWLIST -- an unrecognized or
// omitted value falls back to the server's default (/settings/seats)
// rather than erroring.
export const getWebSessionLink = (target) => client.post('/api/mobile/web_session_link', target ? { target } : {});

// ── Settings (real data + saves) ─────────────────────────────────────
// blueprints/admin_views.py's Bearer twins of the session-only settings
// routes -- SettingsScreen used to show hardcoded fallback text with no
// fetch at all, and every Save button was a no-op alert.
export const fetchSettings = () => client.get('/api/settings');

export const saveCompanySettings = (companyName, companyCode, timezone, workingDays) =>
  client.post('/api/settings/company', {
    company_name: companyName, company_code: companyCode, timezone, working_days: workingDays,
  });

export const saveGeoRadius = (geoRadius, officeLat, officeLon) =>
  client.post('/api/settings/geo_radius', { geo_radius: geoRadius, office_lat: officeLat, office_lon: officeLon });

export const toggleCompanyFeature = (feature, value) =>
  client.post('/api/settings/toggle_feature', { feature, value });

export const saveSalaryRules = (lateDeductionPct, halfDayDeductionPct, graceMinutes, holidayPay, leavePay) =>
  client.post('/api/settings/salary_rules', {
    late_deduction_pct: lateDeductionPct, half_day_deduction_pct: halfDayDeductionPct,
    grace_minutes: graceMinutes, holiday_pay: holidayPay, leave_pay: leavePay,
  });

export const fetchAdminProfile = () => client.get('/api/admin/profile');

export const changeAdminPassword = (currentPassword, newPassword, confirmPassword) =>
  client.post('/api/admin/password', {
    current_password: currentPassword, new_password: newPassword, confirm_password: confirmPassword,
  });

export const registerEmployeePushToken = (expoPushToken, platform) =>
  client.post('/api/employee/push_token', { expo_push_token: expoPushToken, platform });

export const registerAdminPushToken = (expoPushToken, platform) =>
  client.post('/api/push_token', { expo_push_token: expoPushToken, platform });

export const fetchMyQrCode = () => client.get('/api/employee/my_qr');

