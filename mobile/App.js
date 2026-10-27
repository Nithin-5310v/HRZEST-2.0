import React, { useState } from "react";
import { View, ActivityIndicator } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createStackNavigator } from "@react-navigation/stack";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";

import { AuthProvider, useAuth } from "./src/store/AuthContext";
import { ThemeProvider } from "./src/store/ThemeContext";
import LaunchCountdownScreen from "./src/screens/LaunchCountdownScreen";
import LoginScreen from "./src/screens/LoginScreen";
import CompanySignupScreen from "./src/screens/CompanySignupScreen";
import CompanyOtpVerifyScreen from "./src/screens/CompanyOtpVerifyScreen";
import CompanyProfileScreen from "./src/screens/CompanyProfileScreen";
import CompanyDocumentUploadScreen from "./src/screens/CompanyDocumentUploadScreen";
import CompanySignupPendingScreen from "./src/screens/CompanySignupPendingScreen";
import AppLockScreen from "./src/screens/AppLockScreen";
import AdminDrawerNavigator from "./src/navigation/AdminDrawerNavigator";
import EmployeeDrawerNavigator from "./src/navigation/EmployeeDrawerNavigator";
import ManagerDrawerNavigator from "./src/navigation/ManagerDrawerNavigator";
import { getNavigatorKey } from "./src/navigation/roleRouting";
import { initCrashReporting } from "./src/utils/crashReporting";

initCrashReporting();

// Only the logged-out flow needs real stack navigation (Login <-> the 5
// company-signup screens, with back-navigation) -- the post-login
// AdminDrawerNavigator/EmployeeDrawerNavigator trees are untouched below.
const AuthStack = createStackNavigator();

function AuthNavigator() {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name="Login" component={LoginScreen} />
      <AuthStack.Screen name="CompanySignup" component={CompanySignupScreen} />
      <AuthStack.Screen name="CompanyOtpVerify" component={CompanyOtpVerifyScreen} />
      <AuthStack.Screen name="CompanyProfile" component={CompanyProfileScreen} />
      <AuthStack.Screen name="CompanyDocumentUpload" component={CompanyDocumentUploadScreen} />
      <AuthStack.Screen name="CompanySignupPending" component={CompanySignupPendingScreen} />
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

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ThemeProvider>
        <AuthProvider>
          <NavigationContainer>
            <StatusBar style="light" />
            <RootNavigator />
          </NavigationContainer>
        </AuthProvider>
      </ThemeProvider>
    </GestureHandlerRootView>
  );
}
