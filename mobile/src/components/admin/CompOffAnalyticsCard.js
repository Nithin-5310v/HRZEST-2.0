import React from "react";

import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import COMPOFF_THEME from "../../constants/compOffTheme";

export default function CompOffAnalyticsCard({
  weeklyHours,
  monthlyHours,
  averageHours,
  approvalRate,
}) {

  const analytics = [
    {
      title: "Weekly OT",
      value: `${weeklyHours}h`,
      icon: "calendar-outline",
      color: "#0B6E8E",
      background: "#E3F2F5",
    },

    {
      title: "Monthly OT",
      value: `${monthlyHours}h`,
      icon: "time-outline",
      color: "#7C3AED",
      background: "#F3E8FF",
    },

    {
      title: "Average",
      value: `${averageHours}h`,
      icon: "analytics-outline",
      color: "#10B981",
      background: "#ECFDF5",
    },

    {
      title: "Approval",
      value: `${approvalRate}%`,
      icon: "checkmark-done-outline",
      color: "#F59E0B",
      background: "#FEF3C7",
    },
  ];

  return (

    <View style={styles.card}>

      {/* Header */}

      <View style={styles.header}>

        <View>

          <Text style={styles.title}>
            OT Analytics
          </Text>

          <Text style={styles.subtitle}>
            Current Performance Overview
          </Text>

        </View>

        <View style={styles.iconContainer}>

          <Ionicons
            name="bar-chart"
            size={24}
            color={COMPOFF_THEME.colors.primary}
          />

        </View>

      </View>

      {/* Analytics Grid */}

      <View style={styles.grid}>

        {analytics.map((item) => (

          <View
            key={item.title}
            style={styles.statCard}
          >

            <View
              style={[
                styles.statIcon,
                {
                  backgroundColor:
                    item.background,
                },
              ]}
            >

              <Ionicons
                name={item.icon}
                size={22}
                color={item.color}
              />

            </View>

            <Text style={styles.value}>
              {item.value}
            </Text>

            <Text style={styles.label}>
              {item.title}
            </Text>

          </View>

        ))}

      </View>

      {/* Progress */}

      <View style={styles.progressSection}>

        <View style={styles.progressHeader}>

          <Text style={styles.progressTitle}>
            Monthly Target
          </Text>

          <Text style={styles.progressPercent}>
            {approvalRate}%
          </Text>

        </View>

        <View style={styles.progressBackground}>

          <View
            style={[
              styles.progressFill,
              {
                width: `${approvalRate}%`,
              },
            ]}
          />

        </View>

      </View>

    </View>

  );

}

const styles = StyleSheet.create({

  card: {

    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 22,

    marginBottom: 24,

    borderWidth: 1,

    borderColor:
      COMPOFF_THEME.colors.border,

    ...COMPOFF_THEME.shadow,
  },

  header: {

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    marginBottom: 24,
  },

  title: {

    fontSize: 20,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  subtitle: {

    marginTop: 5,

    fontSize: 13,

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  iconContainer: {

    width: 56,

    height: 56,

    borderRadius: 18,

    backgroundColor:
      COMPOFF_THEME.colors.primaryLight,

    justifyContent: "center",

    alignItems: "center",
  },

  grid: {

    flexDirection: "row",

    flexWrap: "wrap",

    justifyContent: "space-between",
  },

  statCard: {

    width: "48%",

    backgroundColor: "#F5FAFB",

    borderRadius: 20,

    paddingVertical: 20,

    alignItems: "center",

    marginBottom: 16,
  },

  statIcon: {

    width: 52,

    height: 52,

    borderRadius: 16,

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 14,
  },

  value: {

    fontSize: 28,

    fontWeight: "900",

    color:
      COMPOFF_THEME.colors.textPrimary,
  },

  label: {

    marginTop: 6,

    fontSize: 13,

    fontWeight: "700",

    color:
      COMPOFF_THEME.colors.textMuted,
  },

  progressSection: {

    marginTop: 8,
  },

  progressHeader: {

    flexDirection: "row",

    justifyContent: "space-between",

    marginBottom: 10,
  },

  progressTitle: {

    fontSize: 14,

    fontWeight: "700",

    color:
      COMPOFF_THEME.colors.textSecondary,
  },

  progressPercent: {

    fontSize: 14,

    fontWeight: "800",

    color:
      COMPOFF_THEME.colors.primary,
  },

  progressBackground: {

    height: 10,

    borderRadius: 20,

    backgroundColor:
      COMPOFF_THEME.colors.divider,

    overflow: "hidden",
  },

  progressFill: {

    height: "100%",

    borderRadius: 20,

    backgroundColor:
      COMPOFF_THEME.colors.primary,
  },

});