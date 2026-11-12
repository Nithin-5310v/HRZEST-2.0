import React from "react";

import {
  View,
  Text,
 TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import SALARY_THEME from "../../../constants/salaryTheme";

export default function PayrollSummaryCard({
  month,
  year,
  totalEmployees,
  totalGross,
  totalNet,
  totalDeductions,
  payrollStatus,
  onGeneratePayroll,
}) {
  return (
    <View style={styles.card}>

      {/* Header */}

      <View style={styles.header}>

        <View>

          <Text style={styles.title}>
            Payroll Summary
          </Text>

          <Text style={styles.subTitle}>
            {month} {year}
          </Text>

        </View>

        <View
          style={[
            styles.statusChip,

            payrollStatus === "Completed"
              ? styles.completedChip
              : styles.draftChip,
          ]}
        >

          <View
            style={[
              styles.statusDot,

              payrollStatus === "Completed"
                ? {
                    backgroundColor:
                      SALARY_THEME.colors.success,
                  }
                : {
                    backgroundColor:
                      SALARY_THEME.colors.warning,
                  },
            ]}
          />

          <Text
            style={[
              styles.statusText,

              payrollStatus === "Completed"
                ? {
                    color:
                      SALARY_THEME.colors.success,
                  }
                : {
                    color:
                      SALARY_THEME.colors.warning,
                  },
            ]}
          >
            {payrollStatus}
          </Text>

        </View>

      </View>

      {/* Net Salary */}

      <Text style={styles.netLabel}>
        Total Net Payroll
      </Text>

      <Text style={styles.netSalary}>
        ₹ {totalNet.toLocaleString()}
      </Text>

      {/* Progress */}

      <View style={styles.progressBackground}>

        <View
          style={styles.progressFill}
        />

      </View>

      {/* Statistics */}

      <View style={styles.statsRow}>

        <View style={styles.statItem}>

          <Ionicons
            name="people-outline"
            size={20}
            color={
              SALARY_THEME.colors.primary
            }
          />

          <Text style={styles.statValue}>
            {totalEmployees}
          </Text>

          <Text style={styles.statLabel}>
            Employees
          </Text>

        </View>

        <View style={styles.divider} />

        <View style={styles.statItem}>

          <Ionicons
            name="wallet-outline"
            size={20}
            color={
              SALARY_THEME.colors.success
            }
          />

          <Text style={styles.statValue}>
            ₹ {totalGross.toLocaleString()}
          </Text>

          <Text style={styles.statLabel}>
            Gross
          </Text>

        </View>

        <View style={styles.divider} />

        <View style={styles.statItem}>

          <Ionicons
            name="remove-circle-outline"
            size={20}
            color={
              SALARY_THEME.colors.danger
            }
          />

          <Text style={styles.statValue}>
            ₹
            {totalDeductions.toLocaleString()}
          </Text>

          <Text style={styles.statLabel}>
            Deductions
          </Text>

        </View>

      </View>

      {/* Generate Button */}

      <TouchableOpacity accessibilityRole="button"
        activeOpacity={0.85}
        style={styles.button}
        onPress={onGeneratePayroll}
      >

        <Ionicons
          name="flash-outline"
          size={20}
          color="#FFFFFF"
        />

        <Text style={styles.buttonText}>
          Generate Payroll
        </Text>

      </TouchableOpacity>

    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor:
      SALARY_THEME.colors.surface,

    borderRadius:
      SALARY_THEME.radius.lg,

    padding: 20,

    borderWidth: 1,

    borderColor:
      SALARY_THEME.colors.border,

    marginBottom: 20,

    ...SALARY_THEME.shadow,
  },

  header: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  title: {
    fontSize: 18,

    fontWeight: "700",

    color:
      SALARY_THEME.colors.textPrimary,
  },

  subTitle: {
    marginTop: 4,

    fontSize: 13,

    color:
      SALARY_THEME.colors.textMuted,
  },

  statusChip: {
    flexDirection: "row",

    alignItems: "center",

    paddingHorizontal: 12,

    paddingVertical: 6,

    borderRadius: 30,
  },

  draftChip: {
    backgroundColor:
      SALARY_THEME.colors.warningLight,
  },

  completedChip: {
    backgroundColor:
      SALARY_THEME.colors.successLight,
  },

  statusDot: {
    width: 8,

    height: 8,

    borderRadius: 4,

    marginRight: 6,
  },

  statusText: {
    fontSize: 13,

    fontWeight: "700",
  },

  netLabel: {
    marginTop: 24,

    fontSize: 13,

    color:
      SALARY_THEME.colors.textMuted,
  },

  netSalary: {
    marginTop: 6,

    fontSize: 34,

    fontWeight: "800",

    color:
      SALARY_THEME.colors.textPrimary,
  },

  progressBackground: {
    marginTop: 22,

    height: 8,

    backgroundColor:
      SALARY_THEME.colors.divider,

    borderRadius: 10,

    overflow: "hidden",
  },

  progressFill: {
    width: "72%",

    height: "100%",

    backgroundColor:
      SALARY_THEME.colors.primary,

    borderRadius: 10,
  },

  statsRow: {
    marginTop: 24,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  statItem: {
    flex: 1,

    alignItems: "center",
  },

  divider: {
    width: 1,

    height: 58,

    backgroundColor:
      SALARY_THEME.colors.divider,
  },

  statValue: {
    marginTop: 10,

    fontSize: 18,

    fontWeight: "800",

    color:
      SALARY_THEME.colors.textPrimary,
  },

  statLabel: {
    marginTop: 4,

    fontSize: 12,

    color:
      SALARY_THEME.colors.textMuted,
  },

  button: {
    marginTop: 26,

    height: 52,

    borderRadius:
      SALARY_THEME.radius.md,

    backgroundColor:
      SALARY_THEME.colors.primary,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",
  },

  buttonText: {
    marginLeft: 10,

    fontSize: 15,

    fontWeight: "700",

    color: "#FFFFFF",
  },
});