import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import SALARY_THEME from "../../../constants/salaryTheme";

export default function SalaryHeader({
  month,
  year,
  onSettingsPress,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.leftSection}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="wallet"
            size={24}
            color={SALARY_THEME.colors.primary}
          />
        </View>

        <View style={styles.textContainer}>
          <Text style={styles.title}>
            Salary & Payslips
          </Text>

          <Text style={styles.subtitle}>
            {month} {year} Payroll Management
          </Text>
        </View>
      </View>

      <TouchableOpacity accessibilityRole="button"
        activeOpacity={0.8}
        style={styles.settingsButton}
        onPress={onSettingsPress}
      >
        <Ionicons
          name="settings-outline"
          size={20}
          color={SALARY_THEME.colors.primary}
        />

        <Text style={styles.settingsText}>
          Payroll Settings
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 12,
    marginBottom: 22,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  leftSection: {
    flex: 1,

    flexDirection: "row",

    alignItems: "center",
  },

  iconContainer: {
    width: 54,

    height: 54,

    borderRadius: 16,

    backgroundColor:
      SALARY_THEME.colors.primaryLight,

    justifyContent: "center",

    alignItems: "center",

    marginRight: 14,
  },

  textContainer: {
    flex: 1,
  },

  title: {
    fontSize: 28,

    fontWeight: "800",

    color:
      SALARY_THEME.colors.textPrimary,
  },

  subtitle: {
    marginTop: 4,

    fontSize: 14,

    color:
      SALARY_THEME.colors.textMuted,

    lineHeight: 20,
  },

  settingsButton: {
    height: 44,

    paddingHorizontal: 16,

    borderRadius: 14,

    backgroundColor:
      SALARY_THEME.colors.surface,

    borderWidth: 1,

    borderColor:
      SALARY_THEME.colors.border,

    flexDirection: "row",

    alignItems: "center",

    justifyContent: "center",

    ...SALARY_THEME.shadow,
  },

  settingsText: {
    marginLeft: 8,

    fontSize: 14,

    fontWeight: "700",

    color:
      SALARY_THEME.colors.primary,
  },
});