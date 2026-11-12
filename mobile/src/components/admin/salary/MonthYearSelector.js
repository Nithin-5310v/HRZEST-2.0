import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import SALARY_THEME from "../../../constants/salaryTheme";

export default function MonthYearSelector({
  selectedMonth,
  selectedYear,
  onMonthPress,
  onYearPress,
  onGeneratePress,
}) {
  return (
    <View style={styles.container}>

      <View style={styles.selectorRow}>

        {/* Month */}

        <TouchableOpacity accessibilityRole="button"
          activeOpacity={0.8}
          style={styles.selectorCard}
          onPress={onMonthPress}
        >

          <Text style={styles.label}>
            Month
          </Text>

          <View style={styles.valueRow}>

            <Text style={styles.value}>
              {selectedMonth}
            </Text>

            <Ionicons
              name="chevron-down"
              size={18}
              color={SALARY_THEME.colors.textMuted}
            />

          </View>

        </TouchableOpacity>

        {/* Year */}

        <TouchableOpacity accessibilityRole="button"
          activeOpacity={0.8}
          style={styles.selectorCard}
          onPress={onYearPress}
        >

          <Text style={styles.label}>
            Year
          </Text>

          <View style={styles.valueRow}>

            <Text style={styles.value}>
              {selectedYear}
            </Text>

            <Ionicons
              name="chevron-down"
              size={18}
              color={SALARY_THEME.colors.textMuted}
            />

          </View>

        </TouchableOpacity>

      </View>

      <TouchableOpacity accessibilityRole="button"
        activeOpacity={0.85}
        style={styles.generateButton}
        onPress={onGeneratePress}
      >

        <Ionicons
          name="document-text-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.generateText}>
          Generate Payroll Report
        </Text>

      </TouchableOpacity>

    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 20,
  },

  selectorRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 14,
  },

  selectorCard: {
    width: "48.5%",

    backgroundColor:
      SALARY_THEME.colors.surface,

    borderRadius:
      SALARY_THEME.radius.lg,

    padding: 16,

    borderWidth: 1,

    borderColor:
      SALARY_THEME.colors.border,

    ...SALARY_THEME.shadow,
  },

  label: {
    fontSize: 12,

    color:
      SALARY_THEME.colors.textMuted,

    marginBottom: 10,

    fontWeight: "600",
  },

  valueRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  value: {
    fontSize: 16,

    fontWeight: "700",

    color:
      SALARY_THEME.colors.textPrimary,
  },

  generateButton: {
    height: 52,

    borderRadius:
      SALARY_THEME.radius.md,

    backgroundColor:
      SALARY_THEME.colors.primary,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    ...SALARY_THEME.shadow,
  },

  generateText: {
    marginLeft: 10,

    color: "#FFFFFF",

    fontSize: 15,

    fontWeight: "700",
  },
});