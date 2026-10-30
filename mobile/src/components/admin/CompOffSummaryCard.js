import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import COMPOFF_THEME from "../../constants/compOffTheme";

const theme = COMPOFF_THEME || {
  colors: {
    textPrimary: "#132026",
    textMuted: "#5A6A70",
  },
};

export default function CompOffSummaryCard({
  totalHours = 0,
  totalRecords = 0,
  pendingApproval = 0,
  availableCompOff = 0,
}) {
  const cards = [
    {
      id: 1,
      title: "OT Hours",
      subtitle: "All Records",
      value: `${Number(totalHours).toFixed(1)}h`,
      icon: "time-outline",
      color: "#0B6E8E",
      background: "#E3F2F5",
      badge: "Live",
      badgeColor: "#DCFCE7",
      badgeText: "#16A34A",
    },
    {
      id: 2,
      title: "OT Records",
      subtitle: "Total Entries",
      value: totalRecords,
      icon: "document-text-outline",
      color: "#10B981",
      background: "#ECFDF5",
      badge: "All Time",
      badgeColor: "#DCFCE7",
      badgeText: "#16A34A",
    },
    {
      id: 3,
      title: "Pending",
      subtitle: "Approval",
      value: pendingApproval,
      icon: "hourglass-outline",
      color: "#F59E0B",
      background: "#FEF3C7",
      badge: "Waiting",
      badgeColor: "#FEF3C7",
      badgeText: "#B45309",
    },
    {
      id: 4,
      title: "Comp-off",
      subtitle: "Available",
      value: availableCompOff,
      icon: "calendar-clear-outline",
      color: "#7C3AED",
      background: "#F3E8FF",
      badge: "Balance",
      badgeColor: "#EDE9FE",
      badgeText: "#7C3AED",
    },
  ];

  return (
    <View style={styles.container}>
      {cards.map((item) => (
        <View key={item.id} style={styles.card}>
          <View
            style={[
              styles.accent,
              { backgroundColor: item.color },
            ]}
          />

          <View style={styles.header}>
            <View
              style={[
                styles.iconContainer,
                { backgroundColor: item.background },
              ]}
            >
              <Ionicons
                name={item.icon}
                size={24}
                color={item.color}
              />
            </View>

            <View
              style={[
                styles.badge,
                { backgroundColor: item.badgeColor },
              ]}
            >
              <Text
                style={[
                  styles.badgeText,
                  { color: item.badgeText },
                ]}
              >
                {item.badge}
              </Text>
            </View>
          </View>

          <Text style={styles.value}>{item.value}</Text>

          <Text style={styles.title}>{item.title}</Text>

          <Text style={styles.subtitle}>{item.subtitle}</Text>

          <View style={styles.divider} />

          <View style={styles.footer}>
            <View style={styles.footerLeft}>
              <Ionicons
                name="analytics-outline"
                size={15}
                color={item.color}
              />
              <Text style={styles.footerText}>
                Live Metrics
              </Text>
            </View>

            <Ionicons
              name="chevron-forward"
              size={18}
              color="#8A9AA0"
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
    marginBottom: 24,
  },

  card: {
    width: "48%",
    backgroundColor: "#FFFFFF",
    borderRadius: 24,
    paddingHorizontal: 18,
    paddingTop: 18,
    paddingBottom: 16,
    marginBottom: 16,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: "#E9F1F3",
    shadowColor: "#132026",
    shadowOpacity: 0.06,
    shadowRadius: 18,
    shadowOffset: {
      width: 0,
      height: 8,
    },
    elevation: 6,
  },

  accent: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    height: 5,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
  },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 4,
    marginBottom: 20,
  },

  iconContainer: {
    width: 56,
    height: 56,
    borderRadius: 18,
    justifyContent: "center",
    alignItems: "center",
  },

  badge: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 20,
    justifyContent: "center",
    alignItems: "center",
  },

  badgeText: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 0.3,
  },

  value: {
    fontSize: 22,
    fontWeight: "800",
    color: theme.colors.textPrimary,
    letterSpacing: -0.4,
  },

  title: {
    marginTop: 6,
    fontSize: 14,
    fontWeight: "700",
    color: theme.colors.textPrimary,
  },

  subtitle: {
    marginTop: 4,
    fontSize: 13,
    fontWeight: "600",
    color: theme.colors.textMuted,
  },

  divider: {
    height: 1,
    backgroundColor: "#E9F1F3",
    marginTop: 18,
    marginBottom: 14,
  },

  footer: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },

  footerLeft: {
    flexDirection: "row",
    alignItems: "center",
  },

  footerText: {
    marginLeft: 6,
    fontSize: 12,
    fontWeight: "700",
    color: "#5A6A70",
  },
});