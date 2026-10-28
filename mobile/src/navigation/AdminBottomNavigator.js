import React from "react";
import { View, StyleSheet } from "react-native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";

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
import NotificationsScreen from "../screens/NotificationsScreen";
import DocumentsScreen from "../screens/admin/DocumentsScreen";
import IdCardScreen from "../screens/IdCardScreen";

const Tab = createBottomTabNavigator();

export default function AdminBottomNavigator() {
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 16);

  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarHideOnKeyboard: true,

        tabBarStyle: {
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 58 + bottomInset,
          backgroundColor: "#0B2253",
          borderTopWidth: 1,
          borderTopColor: "rgba(255, 255, 255, 0.15)",
          borderTopLeftRadius: 22,
          borderTopRightRadius: 22,
          elevation: 25,
          shadowColor: "#000",
          shadowOpacity: 0.3,
          shadowRadius: 16,
          shadowOffset: { width: 0, height: -4 },
          paddingTop: 6,
          paddingBottom: bottomInset,
        },

        tabBarItemStyle: {
          justifyContent: "center",
          alignItems: "center",
          height: 52,
        },

        tabBarActiveTintColor: "#FFFFFF",
        tabBarInactiveTintColor: "rgba(255, 255, 255, 0.65)",

        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: "600",
          letterSpacing: 0.2,
          marginTop: 1,
        },

        tabBarIcon: ({ focused, color }) => {
          let icon;

          switch (route.name) {
            case "Dashboard":
              icon = focused ? "home" : "home-outline";
              break;
            case "Employees":
              icon = focused ? "people" : "people-outline";
              break;
            case "Attendance":
              icon = focused ? "calendar" : "calendar-outline";
              break;
            case "LeaveRequests":
              icon = focused ? "checkmark-done-circle" : "checkmark-done-circle-outline";
              break;
            case "Settings":
              icon = focused ? "settings" : "settings-outline";
              break;
            default:
              icon = "ellipse";
          }

          return (
            <View style={styles.iconContainer}>
              <Ionicons name={icon} size={22} color={color} />
              {focused && <View style={styles.activeDot} />}
            </View>
          );
        },
      })}
    >
      {/* 5 Visible Tabs */}
      <Tab.Screen
        name="Dashboard"
        component={AdminDashboard}
        options={{ tabBarLabel: "Home" }}
      />

      <Tab.Screen
        name="Employees"
        component={EmployeesScreen}
        options={{ tabBarLabel: "Staff" }}
      />

      <Tab.Screen
        name="Attendance"
        component={AttendanceScreen}
        options={{ tabBarLabel: "Logs" }}
      />

      <Tab.Screen
        name="LeaveRequests"
        component={LeaveRequestsScreen}
        options={{ tabBarLabel: "Approvals" }}
      />

      <Tab.Screen
        name="Settings"
        component={SettingsScreen}
        options={{ tabBarLabel: "Settings" }}
      />

      {/* Hidden Screens */}
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
  );
}

const styles = StyleSheet.create({
  iconContainer: {
    alignItems: "center",
    justifyContent: "center",
    height: 26,
  },
  activeDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: "#FFFFFF",
    position: "absolute",
    bottom: -4,
  },
});
