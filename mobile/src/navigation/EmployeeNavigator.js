import React, { useState } from "react";
import { View } from "react-native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { materialTabOptions, MaterialTabIcon, MaterialCenterAction } from "./materialTabs";

import AttendanceScannerModal from "../screens/AttendanceScannerModal";
import EmployeeDashboard from "../screens/employee/EmployeeDashboard";
import LeaveScreen from "../screens/employee/LeaveScreen";
import TicketsScreen from "../screens/employee/TicketsScreen";
import NotificationsScreen from "../screens/NotificationsScreen";

import AttendanceScreen from "../screens/employee/AttendanceScreen";
import CompOffScreen from "../screens/employee/CompOffScreen";
import HolidaysScreen from "../screens/employee/HolidaysScreen";
import EarningsScreen from "../screens/employee/EarningsScreen";
import PayslipsScreen from "../screens/employee/PayslipsScreen";
import PerformanceScreen from "../screens/employee/PerformanceScreen";
import OnboardingScreen from "../screens/employee/OnboardingScreen";
import ProfileScreen from "../screens/employee/ProfileScreen";
import IdCardScreen from "../screens/IdCardScreen";
import PersonalInfoScreen from "../screens/employee/PersonalInfoScreen";
import WorkInfoScreen from "../screens/employee/WorkInfoScreen";
import ContactScreen from "../screens/employee/ContactScreen";
import EmergencyContactScreen from "../screens/employee/EmergencyContactScreen";
import EducationScreen from "../screens/employee/EducationScreen";
import ExperienceScreen from "../screens/employee/ExperienceScreen";
import DocumentsScreen from "../screens/employee/DocumentsScreen";
import BankDetailsScreen from "../screens/employee/BankDetailsScreen";
import SecurityScreen from "../screens/employee/SecurityScreen";
import SettingsScreen from "../screens/employee/SettingsScreen";
import PoliciesScreen from "../screens/employee/PoliciesScreen";
import ResignScreen from "../screens/employee/ResignScreen";
import EmployeeMoreScreen from "../screens/employee/EmployeeMoreScreen";

const Tab = createBottomTabNavigator();

// Tabs + More: Home, Leave, the Scan action, Alerts, More.
const TAB_ICONS = {
  Home: "home",
  Leave: "calendar",
  Notifications: "notifications",
  More: "grid",
};

export default function EmployeeNavigator() {
  const [showScanner, setShowScanner] = useState(false);
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 16);

  return (
    <>
      <AttendanceScannerModal
        visible={showScanner}
        onClose={() => setShowScanner(false)}
        onSuccess={() => setShowScanner(false)}
      />

      <Tab.Navigator
        screenOptions={({ route }) => ({
          ...materialTabOptions(bottomInset),
          tabBarIcon: ({ focused, color }) => (
            <MaterialTabIcon name={TAB_ICONS[route.name] || "ellipse"} focused={focused} color={color} />
          ),
        })}
      >
        {/* 5 Main Tabs */}
        <Tab.Screen
          name="Home"
          component={EmployeeDashboard}
          options={{ tabBarLabel: "Home" }}
        />

        <Tab.Screen
          name="Leave"
          component={LeaveScreen}
          options={{ tabBarLabel: "Leave" }}
        />

        <Tab.Screen
          name="Scan"
          component={View}
          listeners={{
            tabPress: (e) => {
              e.preventDefault();
              setShowScanner(true);
            },
          }}
          options={{
            tabBarLabel: "",
            tabBarIcon: () => <MaterialCenterAction icon="qr-code" />,
          }}
        />

        <Tab.Screen
          name="Notifications"
          component={NotificationsScreen}
          options={{ tabBarLabel: "Alerts" }}
        />

        <Tab.Screen
          name="More"
          component={EmployeeMoreScreen}
          options={{ tabBarLabel: "More" }}
        />

        <Tab.Screen
          name="Tickets"
          component={TicketsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        {/* Hidden Drawer & Profile Screens */}
        <Tab.Screen
          name="Attendance"
          component={AttendanceScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="CompOff"
          component={CompOffScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Holidays"
          component={HolidaysScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Earnings"
          component={EarningsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Payslips"
          component={PayslipsScreen}
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
          name="Profile"
          component={ProfileScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="IdCard"
          component={IdCardScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="PersonalInfo"
          component={PersonalInfoScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="WorkInfo"
          component={WorkInfoScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Contact"
          component={ContactScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="EmergencyContact"
          component={EmergencyContactScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Education"
          component={EducationScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Experience"
          component={ExperienceScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Documents"
          component={DocumentsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="BankDetails"
          component={BankDetailsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Security"
          component={SecurityScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Settings"
          component={SettingsScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Policies"
          component={PoliciesScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />

        <Tab.Screen
          name="Resignation"
          component={ResignScreen}
          options={{ tabBarItemStyle: { display: "none" } }}
        />
      </Tab.Navigator>
    </>
  );
}
