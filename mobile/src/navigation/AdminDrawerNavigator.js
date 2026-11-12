import React from "react";
import { createDrawerNavigator } from "@react-navigation/drawer";
import AdminBottomNavigator from "./AdminBottomNavigator";
import AdminDrawerContent from "../screens/admin/AdminDrawerContent";
import useOfflineSync from "../utils/useOfflineSync";

const Drawer = createDrawerNavigator();

export default function AdminDrawerNavigator() {
  // The admin's own check-ins (Me tab) saved offline; nothing to send for
  // an admin without an employee profile.
  useOfflineSync();
  return (
    <Drawer.Navigator
      initialRouteName="AdminTabs"
      drawerContent={(props) => <AdminDrawerContent {...props} />}
      screenOptions={{
        headerShown: false,
        drawerType: "slide",
        drawerPosition: "left",
        swipeEnabled: true,
        overlayColor: "rgba(15, 23, 42, 0.4)",
        drawerStyle: {
          width: 320,
          backgroundColor: "#F4F6F9",
          borderTopRightRadius: 24,
          borderBottomRightRadius: 24,
        },
        sceneContainerStyle: {
          backgroundColor: "#F4F6F9",
        },
      }}
    >
      <Drawer.Screen name="AdminTabs" component={AdminBottomNavigator} />
    </Drawer.Navigator>
  );
}
