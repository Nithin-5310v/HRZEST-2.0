import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  Switch,
  Alert,
} from "react-native";
import SafeAreaView from "../../components/SafeAreaView";
import * as LocalAuthentication from "expo-local-authentication";

import { Ionicons } from "@expo/vector-icons";

import ProfileHeader from "../../components/profile/ProfileHeader";
import { useAuth } from "../../store/AuthContext";
import { useTheme } from "../../store/ThemeContext";
import {
  getBiometricLockEnabled, setBiometricLockEnabled,
  getNotificationsEnabled, setNotificationsEnabled,
} from "../../utils/preferences";
import {
  requestNotificationPermission, scheduleDailyCheckinReminder, cancelDailyCheckinReminder,
} from "../../utils/localNotifications";
import { fetchEmployeeProfile, updateNotificationPreferences } from "../../api/client";

export default function SettingsScreen({ navigation }) {
  const { signOut } = useAuth();
  const { colors, isDark, toggleTheme } = useTheme();
  const styles = React.useMemo(() => makeStyles(colors), [colors]);
  const [notifications, setNotifications] = useState(true);
  const [biometric, setBiometric] = useState(true);
  const [emailAlerts, setEmailAlerts] = useState(true);

  useEffect(() => {
    (async () => {
      setBiometric(await getBiometricLockEnabled());
      setNotifications(await getNotificationsEnabled());
      try {
        const res = await fetchEmployeeProfile();
        if (res?.data?.ok) setEmailAlerts(!!res.data.profile.email_alerts_enabled);
      } catch (_) {}
    })();
  }, []);

  const handleToggleEmailAlerts = async (value) => {
    setEmailAlerts(value);
    try {
      const res = await updateNotificationPreferences(value);
      if (!res?.data?.ok) throw new Error("failed");
    } catch (e) {
      setEmailAlerts(!value);
      Alert.alert("Update Failed", "Could not update your email alert preference.");
    }
  };

  const handleToggleNotifications = async (value) => {
    if (value) {
      const granted = await requestNotificationPermission();
      if (!granted) {
        Alert.alert(
          "Permission Needed",
          "Enable notifications for this app in your device settings to receive attendance reminders."
        );
        return;
      }
      await scheduleDailyCheckinReminder();
    } else {
      await cancelDailyCheckinReminder();
    }
    setNotifications(value);
    await setNotificationsEnabled(value);
  };

  const handleToggleBiometric = async (value) => {
    if (value) {
      const hasHw = await LocalAuthentication.hasHardwareAsync();
      const isEnrolled = hasHw && await LocalAuthentication.isEnrolledAsync();
      if (!isEnrolled) {
        Alert.alert(
          "Not Available",
          "No fingerprint or Face ID is enrolled on this device. Set it up in your device settings first."
        );
        return;
      }
    }
    setBiometric(value);
    await setBiometricLockEnabled(value);
  };

  const handleLogout = () => {
    Alert.alert("Sign Out", "Are you sure you want to sign out?", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign Out", style: "destructive", onPress: signOut },
    ]);
  };

  const SettingItem = ({
    icon,
    title,
    subtitle,
    right,
    danger = false,
    onPress,
  }) => (
    <TouchableOpacity
      activeOpacity={0.8}
      style={styles.settingCard}
      onPress={onPress}
    >
      <View style={styles.leftSection}>
        <View
          style={[
            styles.iconContainer,
            danger && { backgroundColor: colors.redBg },
          ]}
        >
          <Ionicons
            name={icon}
            size={22}
            color={danger ? colors.danger : colors.primary}
          />
        </View>

        <View style={{ flex: 1 }}>
          <Text
            style={[
              styles.title,
              danger && { color: colors.danger },
            ]}
          >
            {title}
          </Text>

          {subtitle ? (
            <Text style={styles.subtitle}>{subtitle}</Text>
          ) : null}
        </View>
      </View>

      {right}
    </TouchableOpacity>
  );

  return (
    <SafeAreaView style={styles.container}>
      <ProfileHeader
        title="Settings"
        showBack
      />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.content}
      >
        {/* Preferences */}

        <Text style={styles.sectionTitle}>
          Preferences
        </Text>

        <SettingItem
          icon="notifications-outline"
          title="Attendance Reminders"
          subtitle="Daily on-device check-in reminder"
          right={
            <Switch
              value={notifications}
              onValueChange={handleToggleNotifications}
              trackColor={{
                false: colors.border,
                true: colors.primary,
              }}
            />
          }
        />

        <SettingItem
          icon="mail-outline"
          title="Email Alerts"
          subtitle="Leave & resignation status emails"
          right={
            <Switch
              value={emailAlerts}
              onValueChange={handleToggleEmailAlerts}
              trackColor={{
                false: colors.border,
                true: colors.primary,
              }}
            />
          }
        />

        <SettingItem
          icon="moon-outline"
          title="Dark Mode"
          subtitle={isDark ? "On" : "Off"}
          right={
            <Switch
              value={isDark}
              onValueChange={toggleTheme}
              trackColor={{
                false: colors.border,
                true: colors.primary,
              }}
            />
          }
        />

        <Text style={styles.sectionTitle}>
          Security
        </Text>

        <SettingItem
          icon="finger-print-outline"
          title="Biometric Login"
          subtitle="Use fingerprint or Face ID"
          right={
            <Switch
              value={biometric}
              onValueChange={handleToggleBiometric}
              trackColor={{
                false: colors.border,
                true: colors.primary,
              }}
            />
          }
        />

        <SettingItem
          icon="lock-closed-outline"
          title="Change Password"
          subtitle="Update your account password"
          onPress={() => navigation.navigate("Security")}
          right={
            <Ionicons
              name="chevron-forward"
              size={20}
              color={colors.textLight}
            />
          }
        />

        <Text style={styles.sectionTitle}>
          About
        </Text>

        <SettingItem
          icon="information-circle-outline"
          title="About Application"
          subtitle="Version 1.0.0"
          onPress={() => Alert.alert("Employee Attendance", "Enterprise Workforce Platform\nVersion 1.0.0")}
          right={
            <Ionicons
              name="chevron-forward"
              size={20}
              color={colors.textLight}
            />
          }
        />

        <SettingItem
          icon="document-text-outline"
          title="Privacy Policy"
          subtitle="Read our privacy policy"
          onPress={() => navigation.navigate("Policies")}
          right={
            <Ionicons
              name="chevron-forward"
              size={20}
              color={colors.textLight}
            />
          }
        />

        <SettingItem
          icon="shield-checkmark-outline"
          title="Terms & Conditions"
          subtitle="View terms of service"
          onPress={() => navigation.navigate("Policies")}
          right={
            <Ionicons
              name="chevron-forward"
              size={20}
              color={colors.textLight}
            />
          }
        />

        <Text style={styles.sectionTitle}>
          Account
        </Text>

        <SettingItem
          icon="log-out-outline"
          title="Logout"
          subtitle="Sign out from this device"
          danger
          onPress={handleLogout}
          right={
            <Ionicons
              name="chevron-forward"
              size={20}
              color={colors.danger}
            />
          }
        />

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const makeStyles = (colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },

  content: {
    paddingHorizontal: 18,
    paddingBottom: 40,
  },

  sectionTitle: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 14,
    marginTop: 10,
  },

  settingCard: {
    backgroundColor: colors.card,
    borderRadius: 18,
    padding: 16,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",

    shadowColor: "#0F172A",
    shadowOpacity: 0.04,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 3,
    },
    elevation: 2,
  },

  leftSection: {
    flexDirection: "row",
    alignItems: "center",
    flex: 1,
  },

  iconContainer: {
    width: 48,
    height: 48,
    borderRadius: 14,
    backgroundColor: colors.blueBg,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 14,
  },

  title: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.text,
  },

  subtitle: {
    marginTop: 4,
    fontSize: 13,
    color: colors.textSecondary,
    fontWeight: "500",
  },
});
