import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

const DATA = [
  {
    key: "otHours",
    title: "Overtime",
    subtitle: "Hours Worked",
    icon: "time-outline",
    color: "#0B5CAD",
    bg: "#E6EFFA",
  },
  {
    key: "availableDays",
    title: "Available",
    subtitle: "Comp-off Balance",
    icon: "calendar-clear-outline",
    color: "#16A34A",
    bg: "#ECFDF5",
  },
  {
    key: "usedDays",
    title: "Used",
    subtitle: "Days Consumed",
    icon: "trending-down-outline",
    color: "#EA580C",
    bg: "#FFF7ED",
  },
  {
    key: "earnedDays",
    title: "Earned",
    subtitle: "Lifetime Total",
    icon: "sparkles-outline",
    color: "#7C3AED",
    bg: "#F5F3FF",
  },
];

export default function CompOffStatsGrid({
  otHours = "0.0",
  availableDays = "0.0",
  usedDays = "0.0",
  earnedDays = "0.0",
}) {
  const values = {
    otHours,
    availableDays,
    usedDays,
    earnedDays,
  };

  return (
    <View style={styles.container}>
      {DATA.map((item) => (
        <View
          key={item.key}
          style={styles.card}
        >
          {/* Top */}

          <View style={styles.topRow}>
            <View
              style={[
                styles.iconWrapper,
                {
                  backgroundColor: item.bg,
                },
              ]}
            >
              <Ionicons
                name={item.icon}
                size={18}
                color={item.color}
              />
            </View>

            <View style={styles.moreButton}>
              <Ionicons
                name="ellipsis-horizontal"
                size={15}
                color="#8794A5"
              />
            </View>
          </View>

          {/* Center */}

          <View style={styles.content}>
            <Text style={styles.value}>
              {values[item.key]}
            </Text>

            <Text style={styles.title}>
              {item.title}
            </Text>

            <Text style={styles.subtitle}>
              {item.subtitle}
            </Text>
          </View>

          {/* Bottom */}

          <View style={styles.footer}>
            <View style={styles.footerLeft}>
              <View style={styles.dot} />

              <Text style={styles.footerText}>
                Live Data
              </Text>
            </View>

            <Ionicons
              name="arrow-forward"
              size={14}
              color="#C3CFDD"
            />
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",

    marginBottom: 18,
  },

  card: {
    width: "48.2%",

    backgroundColor: "#FFFFFF",

    borderRadius: 18,

    padding: 15,

    marginBottom: 12,

    borderWidth: 1,
    borderColor: "#EDF2F7",

    shadowColor: "#131C2A",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 2,
  },

  topRow: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  iconWrapper: {
    width: 40,
    height: 40,

    borderRadius: 12,

    justifyContent: "center",

    alignItems: "center",
  },

  moreButton: {
    width: 28,
    height: 28,

    borderRadius: 8,

    backgroundColor: "#F5F8FC",

    justifyContent: "center",

    alignItems: "center",
  },

  content: {
    marginTop: 18,
  },

  value: {
    fontSize: 22,

    fontWeight: "800",

    color: "#131C2A",

    letterSpacing: -0.4,
  },

  title: {
    marginTop: 5,

    fontSize: 14,

    fontWeight: "700",

    color: "#2E3A4A",
  },

  subtitle: {
    marginTop: 3,

    fontSize: 11,

    color: "#8794A5",

    fontWeight: "600",
  },

  footer: {
    marginTop: 18,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    paddingTop: 12,

    borderTopWidth: 1,

    borderTopColor: "#EDF2F8",
  },
    footerLeft: {
    flexDirection: "row",
    alignItems: "center",
  },

  dot: {
    width: 7,
    height: 7,
    borderRadius: 4,

    backgroundColor: "#22C55E",

    marginRight: 6,
  },

  footerText: {
    fontSize: 11,

    color: "#8794A5",

    fontWeight: "600",
  },

  trendBadge: {
    position: "absolute",

    top: 14,
    right: 14,

    backgroundColor: "#F5F8FC",

    borderRadius: 20,

    paddingHorizontal: 8,
    paddingVertical: 4,
  },

  trendText: {
    fontSize: 10,

    fontWeight: "700",

    color: "#5A6678",
  },

  divider: {
    height: 1,

    backgroundColor: "#EDF2F8",

    marginVertical: 12,
  },

  metricRow: {
    flexDirection: "row",

    alignItems: "center",

    justifyContent: "space-between",
  },

  metricLeft: {
    flexDirection: "row",

    alignItems: "center",
  },

  metricIcon: {
    width: 28,
    height: 28,

    borderRadius: 8,

    justifyContent: "center",
    alignItems: "center",

    marginRight: 8,
  },

  metricLabel: {
    fontSize: 12,

    color: "#5A6678",

    fontWeight: "600",
  },

  metricNumber: {
    fontSize: 18,

    color: "#131C2A",

    fontWeight: "800",
  },

  cardPressed: {
    transform: [
      {
        scale: 0.98,
      },
    ],
  },
});