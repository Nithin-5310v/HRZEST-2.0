import React from "react";

import MoreGrid from "../../components/MoreGrid";
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
  const { signOut } = useAuth();
  return <MoreGrid navigation={navigation} groups={GROUPS} footer={FOOTER} onSignOut={signOut} />;
}
