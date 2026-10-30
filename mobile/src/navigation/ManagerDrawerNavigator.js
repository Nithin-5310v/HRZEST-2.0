import React from "react";
import { createDrawerNavigator } from "@react-navigation/drawer";
import ManagerBottomNavigator from "./ManagerBottomNavigator";
import ManagerDrawerContent from "../screens/manager/ManagerDrawerContent";

const Drawer = createDrawerNavigator();

export default function ManagerDrawerNavigator() {
  return (
    <Drawer.Navigator
      initialRouteName="ManagerTabs"
      drawerContent={(props) => <ManagerDrawerContent {...props} />}
      screenOptions={{
        headerShown: false,
        drawerType: "slide",
        drawerPosition: "left",
        swipeEnabled: true,
        overlayColor: "rgba(15, 23, 42, 0.4)",
        drawerStyle: {
          width: 320,
          backgroundColor: "#F5FAFB",
          borderTopRightRadius: 24,
          borderBottomRightRadius: 24,
        },
        sceneContainerStyle: {
          backgroundColor: "#F5FAFB",
        },
      }}
    >
      <Drawer.Screen name="ManagerTabs" component={ManagerBottomNavigator} />
    </Drawer.Navigator>
  );
}
