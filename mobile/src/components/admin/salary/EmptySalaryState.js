import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import SALARY_THEME from "../../../constants/salaryTheme";

export default function EmptySalaryState({
  title = "No Payroll Records",
  subtitle = "Generate payroll to create salary records for your employees.",
  buttonText = "Generate Payroll",
  onPress,
}) {
  return (
    <View style={styles.container}>

      <View style={styles.iconContainer}>

        <Ionicons
          name="wallet-outline"
          size={60}
          color={SALARY_THEME.colors.primary}
        />

      </View>

      <Text style={styles.title}>
        {title}
      </Text>

      <Text style={styles.subtitle}>
        {subtitle}
      </Text>

      <TouchableOpacity accessibilityRole="button"
        activeOpacity={0.85}
        style={styles.button}
        onPress={onPress}
      >

        <Ionicons
          name="flash-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.buttonText}>
          {buttonText}
        </Text>

      </TouchableOpacity>

    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor:
      SALARY_THEME.colors.surface,

    borderRadius:
      SALARY_THEME.radius.lg,

    paddingVertical: 40,

    paddingHorizontal: 24,

    alignItems: "center",

    borderWidth: 1,

    borderColor:
      SALARY_THEME.colors.border,

    ...SALARY_THEME.shadow,
  },

  iconContainer: {
    width: 110,

    height: 110,

    borderRadius: 55,

    justifyContent: "center",

    alignItems: "center",

    backgroundColor:
      SALARY_THEME.colors.primaryLight,

    marginBottom: 24,
  },

  title: {
    fontSize: 22,

    fontWeight: "800",

    color:
      SALARY_THEME.colors.textPrimary,

    textAlign: "center",
  },

  subtitle: {
    marginTop: 12,

    fontSize: 15,

    lineHeight: 24,

    color:
      SALARY_THEME.colors.textMuted,

    textAlign: "center",

    marginBottom: 30,
  },

  button: {
    height: 52,

    minWidth: 220,

    borderRadius:
      SALARY_THEME.radius.md,

    backgroundColor:
      SALARY_THEME.colors.primary,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    ...SALARY_THEME.shadow,
  },

  buttonText: {
    marginLeft: 10,

    fontSize: 15,

    fontWeight: "700",

    color: "#FFFFFF",
  },
});