import React from "react";
import { createDrawerNavigator } from "@react-navigation/drawer";
import AttendanceScreen from "../screens/employee/AttendanceScreen";
import EmployeeNavigator from "./EmployeeNavigator";
import EmployeeDrawerContent from "../screens/employee/EmployeeDrawerContent";

const Drawer = createDrawerNavigator();

export default function EmployeeDrawerNavigator() {
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
          backgroundColor: "#F5F8FC",
          borderTopRightRadius: 24,
          borderBottomRightRadius: 24,
        },
        sceneContainerStyle: {
          backgroundColor: "#F5F8FC",
        },
      }}
    >
      <Drawer.Screen name="EmployeeTabs" component={EmployeeNavigator} />
      <Drawer.Screen name="Attendance" component={AttendanceScreen} />
    </Drawer.Navigator>
  );
}