import React, { useEffect, useRef } from "react";
import { AppState } from "react-native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { materialTabOptions, MaterialTabIcon, TabRoots } from "./materialTabs";
import { showsMeTab } from "./roleRouting";
import { useAuth } from "../store/AuthContext";
import { fetchAdminMe } from "../api/client";

import AdminDashboard from "../screens/admin/AdminDashboard";
import EmployeesScreen from "../screens/admin/EmployeesScreen";
import AttendanceScreen from "../screens/admin/AttendanceScreen";
import LeaveRequestsScreen from "../screens/admin/LeaveRequestsScreen";
import SettingsScreen from "../screens/admin/SettingsScreen";

import SalaryPayslipsScreen from "../screens/admin/SalaryPayslipsScreen";
import AnalyticsScreen from "../screens/admin/AnalyticsScreen";
import CompOffScreen from "../screens/admin/CompOffScreen";
import MarkAttendanceScreen from "../screens/admin/MarkAttendanceScreen";
import ResignationsScreen from "../screens/admin/ResignationsScreen";
import AdminTicketsScreen from "../screens/admin/AdminTicketsScreen";
import PerformanceScreen from "../screens/admin/PerformanceScreen";
import OnboardingScreen from "../screens/admin/OnboardingScreen";
import OrgChartScreen from "../screens/admin/OrgChartScreen";
import DepartmentsScreen from "../screens/admin/DepartmentsScreen";
import LeavesHolidaysScreen from "../screens/admin/LeavesHolidaysScreen";
import SeatsBillingScreen from "../screens/admin/SeatsBillingScreen";
import RegistrationCompanyScreen from "../screens/admin/RegistrationCompanyScreen";
import RegistrationDocumentsScreen from "../screens/admin/RegistrationDocumentsScreen";
import OrganizationScreen from "../screens/admin/OrganizationScreen";
import NotificationsScreen from "../screens/NotificationsScreen";
import DocumentsScreen from "../screens/admin/DocumentsScreen";
import IdCardScreen from "../screens/IdCardScreen";
import AdminMoreScreen from "../screens/admin/AdminMoreScreen";
import MyAttendanceScreen from "../screens/employee/AttendanceScreen";

const Tab = createBottomTabNavigator();

// The bottom-bar tabs; every other screen here is an inner page with a back arrow.
const ADMIN_TABS = new Set(["Dashboard", "Employees", "LeaveRequests", "Attendance", "Me", "More"]);

// Tabs + More: five daily tabs (six with Me); everything else opens from More.
const TAB_ICONS = {
  Dashboard: "home",
  Employees: "people",
  LeaveRequests: "checkmark-done-circle",
  Attendance: "calendar",
  Me: "person-circle",
  More: "grid",
};

// Keeps user.linkedEmployeeId in step with the server: asked when the bar
// opens and whenever the app returns to the foreground, so a profile made on
// the website meanwhile brings up the Me tab without signing in again.
function useOwnProfileLink() {
  const { user, updateUser } = useAuth();
  const current = useRef(user?.linkedEmployeeId || null);
  current.current = user?.linkedEmployeeId || null;
  const ownerAdmin = !user?.adminRole || user.adminRole === "admin";

  useEffect(() => {
    if (!ownerAdmin) return undefined;
    let cancelled = false;
    const check = async () => {
      try {
        const { data } = await fetchAdminMe();
        const linked = data?.linked_employee_id || null;
        if (!cancelled && linked !== current.current) updateUser({ linkedEmployeeId: linked });
      } catch (_) {
        // Offline or an older server without /api/admin/me: keep what we have.
      }
    };
    check();
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") check();
    });
    return () => {
      cancelled = true;
      sub.remove();
    };
  }, [ownerAdmin]);
}

export default function AdminBottomNavigator() {
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 16);
  const { user } = useAuth();
  useOwnProfileLink();

  return (
    <TabRoots.Provider value={ADMIN_TABS}>
      <Tab.Navigator
        backBehavior="history"
        screenOptions={({ route }) => ({
          ...materialTabOptions(bottomInset),
          tabBarIcon: ({ focused, color }) => (
            <MaterialTabIcon name={TAB_ICONS[route.name] || "ellipse"} focused={focused} color={color} />
          ),
        })}
      >
        {/* 5 Visible Tabs */}
        <Tab.Screen name="Dashboard" component={AdminDashboard} options={{ tabBarLabel: "Home" }} />
        <Tab.Screen name="Employees" component={EmployeesScreen} options={{ tabBarLabel: "Staff" }} />
        <Tab.Screen name="LeaveRequests" component={LeaveRequestsScreen} options={{ tabBarLabel: "Approvals" }} />
        <Tab.Screen name="Attendance" component={AttendanceScreen} options={{ tabBarLabel: "Attendance" }} />
        {/* The owner admin's own check-in and attendance, on their linked employee profile */}
        {showsMeTab(user) && (
          <Tab.Screen name="Me" component={MyAttendanceScreen} options={{ tabBarLabel: "Me" }} />
        )}
        <Tab.Screen name="More" component={AdminMoreScreen} options={{ tabBarLabel: "More" }} />

        {/* Hidden Screens (opened from More or the dashboard) */}
        <Tab.Screen
          name="Settings"
          component={SettingsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Payroll"
          component={SalaryPayslipsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Analytics"
          component={AnalyticsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="CompOff"
          component={CompOffScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="MarkAttendance"
          component={MarkAttendanceScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Resignations"
          component={ResignationsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Tickets"
          component={AdminTicketsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Performance"
          component={PerformanceScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Onboarding"
          component={OnboardingScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="OrgChart"
          component={OrgChartScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Departments"
          component={DepartmentsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="LeavesHolidays"
          component={LeavesHolidaysScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="SeatsBilling"
          component={SeatsBillingScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        {/* The dashboard's registration checklist (RegistrationChecklistCard) */}
        <Tab.Screen
          name="RegistrationCompany"
          component={RegistrationCompanyScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="RegistrationDocuments"
          component={RegistrationDocumentsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        {/* More > Organization: company details and its terms & policies */}
        <Tab.Screen
          name="Organization"
          component={OrganizationScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Notifications"
          component={NotificationsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Documents"
          component={DocumentsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="IdCard"
          component={IdCardScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />
      </Tab.Navigator>
    </TabRoots.Provider>
  );
}
