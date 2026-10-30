import React, { useState } from "react";
import { View, ActivityIndicator } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createStackNavigator } from "@react-navigation/stack";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider, useAuth } from "./src/store/AuthContext";
import { ThemeProvider } from "./src/store/ThemeContext";
import LaunchCountdownScreen from "./src/screens/LaunchCountdownScreen";
import LoginScreen from "./src/screens/LoginScreen";
import CompanySignupScreen from "./src/screens/CompanySignupScreen";
import AppLockScreen from "./src/screens/AppLockScreen";
import AdminDrawerNavigator from "./src/navigation/AdminDrawerNavigator";
import EmployeeDrawerNavigator from "./src/navigation/EmployeeDrawerNavigator";
import ManagerDrawerNavigator from "./src/navigation/ManagerDrawerNavigator";
import { getNavigatorKey } from "./src/navigation/roleRouting";
import { initCrashReporting } from "./src/utils/crashReporting";

initCrashReporting();

// Only the logged-out flow needs real stack navigation (Login <-> company
// signup, with back-navigation) -- the post-login
// AdminDrawerNavigator/EmployeeDrawerNavigator trees are untouched below.
// Signup is one screen: its code step signs the new admin in, which swaps
// this whole tree for the admin one, where the registration checklist
// carries on.
const AuthStack = createStackNavigator();

function AuthNavigator() {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name="Login" component={LoginScreen} />
      <AuthStack.Screen name="CompanySignup" component={CompanySignupScreen} />
    </AuthStack.Navigator>
  );
}

function RootNavigator() {
  const { user, loading, locked } = useAuth();
  const [showLaunch, setShowLaunch] = useState(true);

  if (loading) {
    return (
      <View
        style={{
          flex: 1,
          backgroundColor: "#0F172A",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <ActivityIndicator size="large" color="#FFFFFF" />
      </View>
    );
  }

  if (!user) {
    if (showLaunch) {
      return (
        <LaunchCountdownScreen
          onContinue={() => setShowLaunch(false)}
        />
      );
    }
    return <AuthNavigator />;
  }

  if (locked) {
    return <AppLockScreen />;
  }

  const navigatorKey = getNavigatorKey(user);

  if (navigatorKey === "manager") {
    return <ManagerDrawerNavigator />;
  }

  if (navigatorKey === "admin") {
    return <AdminDrawerNavigator />;
  }

  if (navigatorKey === "employee") {
    return <EmployeeDrawerNavigator />;
  }

  return <AuthNavigator />;
}

// SafeAreaProvider at the root, not just the one each navigator adds: the
// launch and app-lock screens render outside any navigator, and on web the
// safe-area SafeAreaView throws without a provider above it.
export default function App() {
  return (
    <SafeAreaProvider>
      <GestureHandlerRootView style={{ flex: 1 }}>
        <ThemeProvider>
          <AuthProvider>
            <NavigationContainer>
              <StatusBar style="dark" />
              <RootNavigator />
            </NavigationContainer>
          </AuthProvider>
        </ThemeProvider>
      </GestureHandlerRootView>
    </SafeAreaProvider>
  );
}
