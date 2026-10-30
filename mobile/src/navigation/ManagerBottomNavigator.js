import React from "react";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { materialTabOptions, MaterialTabIcon } from "./materialTabs";

import ManagerDashboard from "../screens/manager/ManagerDashboard";
// Reused directly from the admin screen set -- same data/API
// (fetchLeaveRequests/leaveAction, fetchResignations/resignationAction),
// same server-side role gate (_LEAVE_APPROVER_ROLES includes "manager"
// already), so there is nothing manager-specific to build into these
// screens themselves, only into which navigator mounts them.
import LeaveRequestsScreen from "../screens/admin/LeaveRequestsScreen";
import ResignationsScreen from "../screens/admin/ResignationsScreen";
import CompOffScreen from "../screens/admin/CompOffScreen";
import SettingsScreen from "../screens/admin/SettingsScreen";

const Tab = createBottomTabNavigator();

const TAB_ICONS = {
  Dashboard: "home",
  LeaveRequests: "checkmark-done-circle",
  Resignations: "exit",
  CompOff: "time",
  Settings: "settings",
};

export default function ManagerBottomNavigator() {
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 16);

  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        ...materialTabOptions(bottomInset),
        tabBarIcon: ({ focused, color }) => (
          <MaterialTabIcon name={TAB_ICONS[route.name] || "ellipse"} focused={focused} color={color} />
        ),
      })}
    >
      <Tab.Screen name="Dashboard" component={ManagerDashboard} options={{ tabBarLabel: "Home" }} />
      <Tab.Screen name="LeaveRequests" component={LeaveRequestsScreen} options={{ tabBarLabel: "Leave" }} />
      <Tab.Screen name="Resignations" component={ResignationsScreen} options={{ tabBarLabel: "Resign" }} />
      <Tab.Screen name="CompOff" component={CompOffScreen} options={{ tabBarLabel: "OT" }} />
      <Tab.Screen name="Settings" component={SettingsScreen} options={{ tabBarLabel: "Settings" }} />
    </Tab.Navigator>
  );
}
