import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function NotificationHeaderCard({
  total = 0,
  unread = 0,
}) {
  return (
    <View style={styles.card}>
      <View style={styles.leftContainer}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="notifications"
            size={28}
            color="#FFFFFF"
          />
        </View>

        <View style={styles.textContainer}>
          <Text style={styles.title}>
            Notifications
          </Text>

          <Text style={styles.subtitle}>
            Stay updated with all employee alerts.
          </Text>
        </View>
      </View>

      <View style={styles.statsRow}>
        <View style={styles.statCard}>
          <Text style={styles.statValue}>
            {total}
          </Text>

          <Text style={styles.statLabel}>
            Total
          </Text>
        </View>

        <View style={styles.divider} />

        <View style={styles.statCard}>
          <Text style={styles.unreadValue}>
            {unread}
          </Text>

          <Text style={styles.statLabel}>
            Unread
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#0B5CAD",
    borderRadius: 22,
    padding: 22,
    marginBottom: 22,

    shadowColor: "#0B5CAD",
    shadowOpacity: 0.18,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 6,
    },
    elevation: 8,
  },

  leftContainer: {
    flexDirection: "row",
    alignItems: "center",
  },

  iconContainer: {
    width: 62,
    height: 62,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.18)",

    justifyContent: "center",
    alignItems: "center",
  },

  textContainer: {
    flex: 1,
    marginLeft: 16,
  },

  title: {
    color: "#FFFFFF",
    fontSize: 22,
    fontWeight: "800",
  },

  subtitle: {
    marginTop: 5,
    color: "rgba(255,255,255,0.82)",
    fontSize: 14,
    lineHeight: 20,
  },

  statsRow: {
    marginTop: 22,
    flexDirection: "row",
    backgroundColor: "rgba(255,255,255,0.12)",
    borderRadius: 18,
    paddingVertical: 18,
    alignItems: "center",
  },

  statCard: {
    flex: 1,
    alignItems: "center",
  },

  divider: {
    width: 1,
    height: 40,
    backgroundColor: "rgba(255,255,255,0.25)",
  },

  statValue: {
    color: "#FFFFFF",
    fontSize: 24,
    fontWeight: "800",
  },

  unreadValue: {
    color: "#FACC15",
    fontSize: 24,
    fontWeight: "800",
  },

  statLabel: {
    marginTop: 5,
    color: "#FFFFFF",
    fontSize: 13,
    fontWeight: "600",
  },
});