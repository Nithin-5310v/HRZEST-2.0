import React from "react";

import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";



export default function CompOffStatsGrid({
  approvedRequests,
  pendingRequests,
  rejectedRequests,
  averageHours,
}) {

  const stats = [

    {
      id: 1,

      title: "Approved",

      subtitle: "Completed",

      value: approvedRequests,

      icon: "checkmark-circle",

      color: "#10B981",

      background: "#ECFDF5",

      badge: "Live",

      badgeColor: "#DCFCE7",

      badgeText: "#15803D",
    },

    {
      id: 2,

      title: "Pending",

      subtitle: "Waiting",

      value: pendingRequests,

      icon: "time",

      color: "#F59E0B",

      background: "#FEF3C7",

      badge: "Review",

      badgeColor: "#FEF3C7",

      badgeText: "#B45309",
    },

    {
      id: 3,

      title: "Rejected",

      subtitle: "Declined",

      value: rejectedRequests,

      icon: "close-circle",

      color: "#EF4444",

      background: "#FEE2E2",

      badge: "Closed",

      badgeColor: "#FEE2E2",

      badgeText: "#B91C1C",
    },

    {
      id: 4,

      title: "Average OT",

      subtitle: "Per Employee",

      value: `${averageHours}h`,

      icon: "analytics",

      color: "#0B5CAD",

      background: "#D3E3F7",

      badge: "Monthly",

      badgeColor: "#D3E3F7",

      badgeText: "#094A8C",
    },

  ];

  return (

    <View style={styles.container}>

      {stats.map((item) => (

        <View
          key={item.id}
          style={styles.card}
        >

          {/* Top Accent */}

          <View
            style={[
              styles.accent,
              {
                backgroundColor:
                  item.color,
              },
            ]}
          />

          {/* Header */}

          <View style={styles.header}>

            <View
              style={[
                styles.iconContainer,
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

            <View
              style={[
                styles.badge,
                {
                  backgroundColor:
                    item.badgeColor,
                },
              ]}
            >

              <Text
                style={[
                  styles.badgeText,
                  {
                    color:
                      item.badgeText,
                  },
                ]}
              >
                {item.badge}
              </Text>

            </View>

          </View>
                    {/* KPI Value */}

          <Text style={styles.value}>
            {item.value}
          </Text>

          {/* Title */}

          <Text style={styles.title}>
            {item.title}
          </Text>

          {/* Subtitle */}

          <Text style={styles.subtitle}>
            {item.subtitle}
          </Text>

          {/* Divider */}

          <View style={styles.divider} />

          {/* Footer */}

          <View style={styles.footer}>

            <View style={styles.footerLeft}>

              <Ionicons
                name="pulse-outline"
                size={15}
                color={item.color}
              />

              <Text style={styles.footerText}>
                Updated Today
              </Text>

            </View>

            <View
              style={[
                styles.dot,
                {
                  backgroundColor: item.color,
                },
              ]}
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
    width: "48.8%",

    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    paddingHorizontal: 18,

    paddingTop: 18,

    paddingBottom: 16,

    marginBottom: 16,

    overflow: "hidden",

    borderWidth: 1,

    borderColor: "#E9EFF6",

    shadowColor: "#131C2A",

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

    marginBottom: 18,
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

    color: "#131C2A",

    letterSpacing: -0.4,
  },

  title: {
    marginTop: 8,

    fontSize: 16,

    fontWeight: "800",

    color: "#131C2A",
  },

  subtitle: {
    marginTop: 4,

    fontSize: 13,

    fontWeight: "600",

    color: "#5A6678",
  },
    divider: {
    height: 1,

    backgroundColor: "#E9EFF6",

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

    flex: 1,
  },

  footerText: {
    marginLeft: 6,

    fontSize: 12,

    fontWeight: "700",

    color: "#5A6678",
  },

  dot: {
    width: 10,

    height: 10,

    borderRadius: 5,
  },

});