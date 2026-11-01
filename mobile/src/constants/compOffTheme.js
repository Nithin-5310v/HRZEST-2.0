export const compOffSummary = {
  month: "July",
  year: "2026",

  totalOtHours: 18.5,

  otPay: 4650,

  pendingApproval: 2,

  compOffAvailable: 3.5,

  totalRequests: 12,

  approvedRequests: 9,

  rejectedRequests: 1,
};

export const overtimeHistory = [
  {
    id: "OT001",

    employeeId: "EMP101",

    employeeName: "Ramesh Kumar",

    department: "Engineering",

    date: "08 Jul 2026",

    checkIn: "09:00 AM",

    checkOut: "09:15 PM",

    overtimeHours: 3.25,

    overtimePay: 975,

    status: "Approved",

    approver: "HR Manager",

    reason: "Production deployment",

    compOffEarned: 0.5,
  },

  {
    id: "OT002",

    employeeId: "EMP102",

    employeeName: "Priya Sharma",

    department: "Design",

    date: "07 Jul 2026",

    checkIn: "09:30 AM",

    checkOut: "08:40 PM",

    overtimeHours: 2.0,

    overtimePay: 600,

    status: "Pending",

    approver: "",

    reason: "Client revisions",

    compOffEarned: 0,
  },

  {
    id: "OT003",

    employeeId: "EMP103",

    employeeName: "Arjun Patel",

    department: "QA",

    date: "05 Jul 2026",

    checkIn: "09:00 AM",

    checkOut: "10:05 PM",

    overtimeHours: 4.5,

    overtimePay: 1350,

    status: "Approved",

    approver: "HR Manager",

    reason: "Regression testing",

    compOffEarned: 1,
  },
];

export const compOffBalances = [
  {
    id: "CO001",

    employeeName: "Ramesh Kumar",

    availableDays: 3.5,

    usedDays: 1,

    remainingDays: 2.5,

    expiryDate: "31 Dec 2026",
  },

  {
    id: "CO002",

    employeeName: "Priya Sharma",

    availableDays: 2,

    usedDays: 0,

    remainingDays: 2,

    expiryDate: "31 Dec 2026",
  },
];

export const analytics = {
  weeklyHours: 7.5,

  monthlyHours: 18.5,

  averageHours: 2.6,

  approvalRate: 91,
};

export const compOffPolicies = [
  {
    id: 1,
    title: "Maximum OT",
    value: "4 Hours / Day",
  },

  {
    id: 2,
    title: "Comp-Off Credit",
    value: "1 Day per 8 OT Hours",
  },

  {
    id: 3,
    title: "Approval Required",
    value: "Manager + HR",
  },

  {
    id: 4,
    title: "Expiry",
    value: "6 Months",
  },
];

export const monthOptions = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export const yearOptions = [
  "2024",
  "2025",
  "2026",
  "2027",
];

const COMPOFF_THEME = {
  colors: {
    // Primary
    primary: "#1D4ED8",
    primaryLight: "#DBEAFE",

    // Secondary
    secondary: "#3B82F6",

    // Backgrounds
    background: "#F4F6F9",

    // Text
    textPrimary: "#101828",
    textSecondary: "#475467",
    textMuted: "#475467",

    // Borders & Dividers
    border: "#E4E7EC",
    divider: "#EEF1F5",

    // Status
    success: "#22C55E",
    successLight: "#DCFCE7",
    warning: "#F59E0B",
    warningLight: "#FEF3C7",
    danger: "#EF4444",
    dangerLight: "#FEE2E2",
  },
};

export default COMPOFF_THEME;