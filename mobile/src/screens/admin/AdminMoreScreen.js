import React from "react";
import { Alert } from "react-native";

import MoreGrid from "../../components/MoreGrid";
import { showsMeTab } from "../../navigation/roleRouting";
import { useAuth } from "../../store/AuthContext";

// The admin's More tab: the screens that used to sit in the side menu.
// Routes are AdminBottomNavigator's hidden tabs.
const GROUPS = [
  {
    title: "People",
    items: [
      { label: "Onboarding", icon: "flag", route: "Onboarding" },
      { label: "Performance", icon: "star", route: "Performance" },
      { label: "Resignations", icon: "exit", route: "Resignations" },
      { label: "Org chart", icon: "git-network", route: "OrgChart" },
    ],
  },
  {
    title: "Time",
    items: [
      { label: "Leave & holidays", icon: "calendar", route: "LeavesHolidays" },
      { label: "Overtime", icon: "timer", route: "CompOff" },
      { label: "Mark attendance", icon: "create", route: "MarkAttendance" },
      { label: "Departments", icon: "business", route: "Departments" },
    ],
  },
  {
    title: "Money",
    items: [
      { label: "Payroll", icon: "wallet", route: "Payroll" },
      { label: "Seats & billing", icon: "card", route: "SeatsBilling" },
    ],
  },
  {
    title: "Company",
    items: [
      { label: "Analytics", icon: "bar-chart", route: "Analytics" },
      { label: "Broadcast", icon: "megaphone", route: "Notifications" },
      { label: "Documents", icon: "document-text", route: "Documents" },
      { label: "Tickets", icon: "help-buoy", route: "Tickets" },
    ],
  },
];

const FOOTER = [
  { label: "Organization", detail: "Company details, terms & policies", route: "Organization" },
  { label: "Company settings", detail: "Shifts, geofence, billing", route: "Settings" },
];

export default function AdminMoreScreen({ navigation }) {
  const { user, signOut, switchToEmployeeView } = useAuth();
  // HR staff are employees too, and so is an owner admin who made their own
  // employee profile: their own leave, payslips and attendance, like the
  // website's "My Profile" button.
  const switchRow = {
    label: "My employee view",
    detail: "Your own leave, payslips, attendance",
    onPress: async () => {
      try {
        await switchToEmployeeView();
      } catch (err) {
        Alert.alert("Can't Switch", err?.response?.data?.msg || "Please try again.");
      }
    },
  };
  const footer = user?.adminRole === "hr" || showsMeTab(user) ? [switchRow, ...FOOTER] : FOOTER;
  return <MoreGrid navigation={navigation} groups={GROUPS} footer={footer} onSignOut={signOut} />;
}
