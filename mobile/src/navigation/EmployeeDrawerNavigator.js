import React from "react";
import { createDrawerNavigator } from "@react-navigation/drawer";
import AttendanceScreen from "../screens/employee/AttendanceScreen";
import EmployeeNavigator from "./EmployeeNavigator";
import useOfflineSync from "../utils/useOfflineSync";
import EmployeeDrawerContent from "../screens/employee/EmployeeDrawerContent";

const Drawer = createDrawerNavigator();

export default function EmployeeDrawerNavigator() {
  // Punches saved offline go out from anywhere in the app, not just the Dashboard.
  useOfflineSync();
  return (
    <Drawer.Navigator
      initialRouteName="EmployeeTabs"
      drawerContent={(props) => <EmployeeDrawerContent {...props} />}
      screenOptions={{
        headerShown: false,
        drawerType: "slide",
        drawerPosition: "left",
        swipeEnabled: true,
        overlayColor: "rgba(15, 23, 42, 0.4)",
        drawerStyle: {
          width: 310,
          backgroundColor: "#F4F6F9",
          borderTopRightRadius: 24,
          borderBottomRightRadius: 24,
        },
        sceneContainerStyle: {
          backgroundColor: "#F4F6F9",
        },
      }}
    >
      <Drawer.Screen name="EmployeeTabs" component={EmployeeNavigator} />
      <Drawer.Screen name="Attendance" component={AttendanceScreen} />
    </Drawer.Navigator>
  );
}