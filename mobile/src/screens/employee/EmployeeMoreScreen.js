import React from "react";

import MoreGrid from "../../components/MoreGrid";
import { employeeLogout } from "../../api/client";
import { useAuth } from "../../store/AuthContext";

// The employee's More tab: the screens that used to sit in the side menu.
// Routes are EmployeeNavigator's hidden tabs.
const GROUPS = [
  {
    title: "Time",
    items: [
      { label: "Attendance log", icon: "time", route: "Attendance" },
      { label: "Overtime", icon: "timer", route: "CompOff" },
      { label: "Holidays", icon: "sunny", route: "Holidays" },
      { label: "Help desk", icon: "help-buoy", route: "Tickets" },
    ],
  },
  {
    title: "Money",
    items: [
      { label: "Payslips", icon: "wallet", route: "Payslips" },
      { label: "Earnings", icon: "bar-chart", route: "Earnings" },
      { label: "Bank details", icon: "business", route: "BankDetails" },
      { label: "Documents", icon: "document-text", route: "Documents" },
    ],
  },
  {
    title: "Me",
    items: [
      { label: "Profile", icon: "person", route: "Profile" },
      { label: "ID card", icon: "id-card", route: "IdCard" },
      { label: "Performance", icon: "star", route: "Performance" },
      { label: "Onboarding", icon: "flag", route: "Onboarding" },
    ],
  },
  {
    title: "Company",
    items: [
      { label: "Policies", icon: "book", route: "Policies" },
      { label: "Resignation", icon: "exit", route: "Resignation" },
    ],
  },
];

const FOOTER = [{ label: "Settings", detail: "Notifications, security", route: "Settings" }];

export default function EmployeeMoreScreen({ navigation }) {
  const { signOut } = useAuth();
  const handleSignOut = async () => {
    try {
      await employeeLogout();
    } catch (_) {}
    signOut();
  };
  return <MoreGrid navigation={navigation} groups={GROUPS} footer={FOOTER} onSignOut={handleSignOut} />;
}
