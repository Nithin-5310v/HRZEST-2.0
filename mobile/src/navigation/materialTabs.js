import React from "react";
import { View, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";

// The Material You bottom bar shared by the admin, employee and manager
// tab navigators: a blue-tinted bar, a pill behind the active icon, navy
// labels -- the logo's palette (see theme/webAuthStyle.js's WEB).
export const NAV = {
  bar: "#E7EEF7",
  pill: "#D3E3F7",
  active: "#0C3054",
  inactive: "#455163",
  accent: "#0B5CAD",
};

/** Screen options for a Tab.Navigator; spread them in, then add tabBarIcon. */
export function materialTabOptions(bottomInset) {
  return {
    headerShown: false,
    tabBarHideOnKeyboard: true,
    tabBarStyle: {
      position: "absolute",
      left: 0,
      right: 0,
      bottom: 0,
      height: 68 + bottomInset,
      backgroundColor: NAV.bar,
      borderTopWidth: 0,
      elevation: 0,
      shadowOpacity: 0,
      paddingTop: 8,
      paddingBottom: bottomInset,
    },
    tabBarItemStyle: { justifyContent: "center", alignItems: "center", height: 60 },
    tabBarActiveTintColor: NAV.active,
    tabBarInactiveTintColor: NAV.inactive,
    tabBarLabelStyle: { fontSize: 12, fontWeight: "600", marginTop: 4 },
  };
}

/** A tab icon in its pill: filled and on the pill when focused, outlined
 *  otherwise. `name` is the Ionicons base name ("home", not "home-outline"). */
export function MaterialTabIcon({ name, focused, color }) {
  return (
    <View style={[styles.pill, focused && styles.pillOn]}>
      <Ionicons name={focused ? name : `${name}-outline`} size={22} color={color} />
    </View>
  );
}

/** The raised centre action (the employee bar's Scan button). */
export function MaterialCenterAction({ icon }) {
  return (
    <View style={styles.center}>
      <Ionicons name={icon} size={28} color="#FFFFFF" />
    </View>
  );
}

const styles = StyleSheet.create({
  pill: { width: 60, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  pillOn: { backgroundColor: NAV.pill },
  center: {
    width: 58,
    height: 58,
    borderRadius: 18,
    backgroundColor: NAV.accent,
    alignItems: "center",
    justifyContent: "center",
    top: -12,
    elevation: 6,
    shadowColor: NAV.accent,
    shadowOpacity: 0.35,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
});
