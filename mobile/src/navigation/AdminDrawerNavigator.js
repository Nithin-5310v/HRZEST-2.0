import React from "react";
import { createDrawerNavigator } from "@react-navigation/drawer";
import AdminBottomNavigator from "./AdminBottomNavigator";
import AdminDrawerContent from "../screens/admin/AdminDrawerContent";

const Drawer = createDrawerNavigator();

export default function AdminDrawerNavigator() {
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
          backgroundColor: "#F5F8FC",
          borderTopRightRadius: 24,
          borderBottomRightRadius: 24,
        },
        sceneContainerStyle: {
          backgroundColor: "#F5F8FC",
        },
      }}
    >
      <Drawer.Screen name="AdminTabs" component={AdminBottomNavigator} />
    </Drawer.Navigator>
  );
}
