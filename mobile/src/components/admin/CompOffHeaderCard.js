import React from "react";

import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const COMPOFF_THEME = {
  colors: {
    success: "#16A34A",
    successLight: "#DCFCE7",
    textPrimary: "#132026",
    textMuted: "#5A6A70",
  },
};

export default function CompOffHeaderCard({
  month,
  year,
  totalHours,
  availableCompOff,
  onSettings,
}) {
  const progress = Math.min(
    (totalHours / 40) * 100,
    100
  );

  return (
    <View style={styles.card}>

      {/* Decorative Background */}

      <View style={styles.circleOne} />
      <View style={styles.circleTwo} />

      {/* ===========================
            HEADER
      =========================== */}

      <View style={styles.header}>

        <View style={styles.leftSection}>

          <View style={styles.iconContainer}>

            <Ionicons
              name="time-outline"
              size={26}
              color="#FFFFFF"
            />

          </View>

          <View>

            <Text style={styles.title}>
              OT & Comp-off
            </Text>

            <Text style={styles.subtitle}>
              Workforce Overtime Management
            </Text>

          </View>

        </View>

        <TouchableOpacity
          activeOpacity={0.85}
          style={styles.settingsButton}
          onPress={onSettings}
        >

          <Ionicons
            name="options-outline"
            size={22}
            color="#FFFFFF"
          />

        </TouchableOpacity>

      </View>

      {/* ===========================
            MONTH CHIP
      =========================== */}

      <View style={styles.monthChip}>

        <Ionicons
          name="calendar-outline"
          size={15}
          color="#FFFFFF"
        />

        <Text style={styles.monthText}>
          {month} {year}
        </Text>

      </View>

      {/* ===========================
            HERO SECTION
      =========================== */}

      <View style={styles.heroSection}>

        <View style={{ flex: 1 }}>

          <Text style={styles.heroLabel}>
            Total Overtime
          </Text>

          <Text style={styles.heroValue}>
            {Number(totalHours).toFixed(1)}
            <Text style={styles.heroUnit}>
              h
            </Text>
          </Text>

        </View>
                  {/* Floating Balance Card */}

          <View style={styles.balanceCard}>

            <View style={styles.balanceIcon}>

              <Ionicons
                name="calendar-clear"
                size={24}
                color={COMPOFF_THEME.colors.success}
              />

            </View>

            <Text style={styles.balanceValue}>
              {availableCompOff}
            </Text>

            <Text style={styles.balanceLabel}>
              Comp-off Days
            </Text>

            <View style={styles.balanceBadge}>

              <Ionicons
                name="checkmark-circle"
                size={13}
                color="#10B981"
              />

              <Text style={styles.balanceBadgeText}>
                Available
              </Text>

            </View>

          </View>

        </View>

      

      {/* ===========================
            KPI CARDS
      =========================== */}

      <View style={styles.statsRow}>

        <View style={styles.statCard}>

          <View
            style={[
              styles.statIcon,
              {
                backgroundColor:
                  "rgba(255,255,255,0.16)",
              },
            ]}
          >

            <Ionicons
              name="flash-outline"
              size={20}
              color="#FFFFFF"
            />

          </View>

          <View style={styles.statContent}>

            <Text style={styles.statValue}>
              {Number(totalHours).toFixed(1)}h
            </Text>

            <Text style={styles.statTitle}>
              OT Logged
            </Text>

          </View>

        </View>

        <View style={styles.statCard}>

          <View
            style={[
              styles.statIcon,
              {
                backgroundColor:
                  "rgba(255,255,255,0.16)",
              },
            ]}
          >

            <Ionicons
              name="calendar-number-outline"
              size={20}
              color="#FFFFFF"
            />

          </View>

          <View style={styles.statContent}>

            <Text style={styles.statValue}>
              {availableCompOff}
            </Text>

            <Text style={styles.statTitle}>
              Balance
            </Text>

          </View>

        </View>

      </View>

      {/* ===========================
            MONTHLY UTILIZATION
      =========================== */}

      <View style={styles.progressContainer}>

        <View style={styles.progressHeader}>

          <Text style={styles.progressTitle}>
            Monthly Utilization
          </Text>

          <Text style={styles.progressPercent}>
            {Math.round(progress)}%
          </Text>

        </View>

        <View style={styles.progressTrack}>

          <View
            style={[
              styles.progressFill,
              {
                width: `${progress}%`,
              },
            ]}
          />

        </View>

      </View>

      {/* ===========================
            FOOTER
      =========================== */}

      <View style={styles.footer}>

        <View style={styles.footerItem}>

          <Ionicons
            name="alarm-outline"
            size={16}
            color="rgba(255,255,255,0.85)"
          />

          <Text style={styles.footerText}>
            40h Monthly Limit
          </Text>

        </View>

        <View style={styles.footerDivider} />

        <View style={styles.footerItem}>

          <Ionicons
            name="shield-checkmark-outline"
            size={16}
            color="rgba(255,255,255,0.85)"
          />

          <Text style={styles.footerText}>
            HR Policy Active
          </Text>

        </View>

      </View>

    </View>

  );

}
const styles = StyleSheet.create({

  card: {
    backgroundColor: "#0C3054",

    borderRadius: 30,

    paddingHorizontal: 24,

    paddingTop: 24,

    paddingBottom: 24,

    marginBottom: 24,

    overflow: "hidden",

    shadowColor: "#0C3054",

    shadowOpacity: 0.28,

    shadowRadius: 22,

    shadowOffset: {
      width: 0,
      height: 12,
    },

    elevation: 16,
  },

  circleOne: {
    position: "absolute",

    top: -70,

    right: -60,

    width: 190,

    height: 190,

    borderRadius: 95,

    backgroundColor: "rgba(255,255,255,0.06)",
  },

  circleTwo: {
    position: "absolute",

    bottom: -90,

    left: -60,

    width: 170,

    height: 170,

    borderRadius: 85,

    backgroundColor: "rgba(255,255,255,0.05)",
  },

  header: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    zIndex: 2,
  },

  leftSection: {
    flexDirection: "row",

    alignItems: "center",
  },

  iconContainer: {
    width: 58,

    height: 58,

    borderRadius: 18,

    backgroundColor: "rgba(255,255,255,0.16)",

    justifyContent: "center",

    alignItems: "center",

    marginRight: 16,
  },

  title: {
    fontSize: 24,

    fontWeight: "900",

    color: "#FFFFFF",

    letterSpacing: -0.6,
  },

  subtitle: {
    marginTop: 4,

    fontSize: 13,

    color: "rgba(255,255,255,0.75)",
  },

  settingsButton: {
    width: 48,

    height: 48,

    borderRadius: 16,

    backgroundColor: "rgba(255,255,255,0.14)",

    justifyContent: "center",

    alignItems: "center",
  },

  monthChip: {
    alignSelf: "flex-start",

    marginTop: 22,

    paddingHorizontal: 14,

    paddingVertical: 8,

    borderRadius: 50,

    backgroundColor: "rgba(255,255,255,0.12)",

    flexDirection: "row",

    alignItems: "center",
  },

  monthText: {
    marginLeft: 6,

    color: "#FFFFFF",

    fontWeight: "700",

    fontSize: 13,
  },

  heroSection: {
    marginTop: 28,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  heroLabel: {
    fontSize: 15,

    color: "rgba(255,255,255,0.80)",

    fontWeight: "600",
  },

  heroValue: {
    marginTop: 6,

    fontSize: 30,

    fontWeight: "800",

    color: "#FFFFFF",

    letterSpacing: -0.6,
  },

  heroUnit: {
    fontSize: 18,

    fontWeight: "700",

    color: "rgba(255,255,255,0.82)",
  },

  trendRow: {
    flexDirection: "row",

    alignItems: "center",

    marginTop: 14,
  },

  trendChip: {
    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#FFFFFF",

    paddingHorizontal: 10,

    paddingVertical: 5,

    borderRadius: 30,
  },

  trendText: {
    marginLeft: 4,

    fontSize: 12,

    fontWeight: "800",

    color: "#10B981",
  },

  compareText: {
    marginLeft: 10,

    color: "rgba(255,255,255,0.78)",

    fontSize: 12,

    fontWeight: "600",
  },

  balanceCard: {
    width: 140,

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    paddingVertical: 18,

    paddingHorizontal: 14,

    alignItems: "center",

    shadowColor: "#000",

    shadowOpacity: 0.08,

    shadowRadius: 14,

    shadowOffset: {
      width: 0,
      height: 8,
    },

    elevation: 8,
  },

  balanceIcon: {
    width: 54,

    height: 54,

    borderRadius: 16,

    backgroundColor:
      COMPOFF_THEME.colors.successLight,

    justifyContent: "center",

    alignItems: "center",
  },

  balanceValue: {
    marginTop: 10,

    fontSize: 22,

    fontWeight: "800",

    color: COMPOFF_THEME.colors.textPrimary,

    letterSpacing: -0.4,
  },

  balanceLabel: {
    marginTop: 4,

    fontSize: 12,

    fontWeight: "700",

    color: COMPOFF_THEME.colors.textMuted,

    textAlign: "center",
  },

  balanceBadge: {
    marginTop: 14,

    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#ECFDF5",

    paddingHorizontal: 10,

    paddingVertical: 5,

    borderRadius: 20,
  },

  balanceBadgeText: {
    marginLeft: 5,

    fontSize: 11,

    fontWeight: "800",

    color: "#10B981",
  },
    statsRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    marginTop: 28,
  },

  statCard: {
    flex: 1,

    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "rgba(255,255,255,0.10)",

    borderRadius: 18,

    paddingVertical: 16,

    paddingHorizontal: 16,

    marginHorizontal: 4,
  },

  statIcon: {
    width: 42,

    height: 42,

    borderRadius: 14,

    justifyContent: "center",

    alignItems: "center",

    marginRight: 12,
  },

  statContent: {
    flex: 1,
  },

  statValue: {
    fontSize: 22,

    fontWeight: "900",

    color: "#FFFFFF",

    letterSpacing: -0.5,
  },

  statTitle: {
    marginTop: 3,

    fontSize: 12,

    fontWeight: "600",

    color: "rgba(255,255,255,0.72)",
  },

  progressContainer: {
    marginTop: 28,
  },

  progressHeader: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    marginBottom: 10,
  },

  progressTitle: {
    fontSize: 14,

    fontWeight: "700",

    color: "#FFFFFF",
  },

  progressPercent: {
    fontSize: 14,

    fontWeight: "800",

    color: "#FFFFFF",
  },

  progressTrack: {
    height: 10,

    borderRadius: 30,

    backgroundColor: "rgba(255,255,255,0.18)",

    overflow: "hidden",
  },

  progressFill: {
    height: "100%",

    borderRadius: 30,

    backgroundColor: "#22C55E",
  },

  footer: {
    marginTop: 24,

    paddingTop: 18,

    borderTopWidth: 1,

    borderTopColor: "rgba(255,255,255,0.12)",

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  footerItem: {
    flex: 1,

    flexDirection: "row",

    alignItems: "center",

    justifyContent: "center",
  },

  footerDivider: {
    width: 1,

    height: 22,

    backgroundColor: "rgba(255,255,255,0.20)",
  },

  footerText: {
    marginLeft: 8,

    fontSize: 12,

    fontWeight: "700",

    color: "rgba(255,255,255,0.82)",
  },

});