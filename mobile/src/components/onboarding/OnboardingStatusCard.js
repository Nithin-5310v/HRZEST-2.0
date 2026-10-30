import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

export default function OnboardingStatusCard({
  employeeName,
  employeeId,
  status,
  progress = 0,
  department = "—",
  joinedDate = "—",
}) {
  const statusColor =
    status === "Completed"
      ? "#22C55E"
      : status === "In Progress"
      ? "#F59E0B"
      : "#EF4444";

  return (
    <LinearGradient
      colors={["#0B6E8E", "#2955C8"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.container}
    >
      <View style={styles.topRow}>
        <View style={styles.avatar}>
          <Ionicons
            name="person"
            size={44}
            color="#0B6E8E"
          />
        </View>

        <View style={styles.info}>
          <Text style={styles.name}>
            {employeeName}
          </Text>

          <Text style={styles.id}>
            Employee ID • {employeeId}
          </Text>

          <View
            style={[
              styles.statusBadge,
              {
                backgroundColor: statusColor,
              },
            ]}
          >
            <Ionicons
              name={
                status === "Completed"
                  ? "checkmark-circle"
                  : "time"
              }
              size={14}
              color="#FFF"
            />

            <Text style={styles.statusText}>
              {status.toUpperCase()}
            </Text>
          </View>
        </View>

        <View style={styles.progressCircle}>
          <Text style={styles.progressValue}>
            {progress}%
          </Text>

          <Text style={styles.progressLabel}>
            Complete
          </Text>
        </View>
      </View>

      <View style={styles.divider} />

      <View style={styles.bottomRow}>
        <View style={styles.item}>
          <Ionicons
            name="business-outline"
            size={18}
            color="#BFD6FF"
          />

          <Text style={styles.itemTitle}>
            Department
          </Text>

          <Text style={styles.itemValue}>
            {department}
          </Text>
        </View>

        <View style={styles.item}>
          <Ionicons
            name="calendar-outline"
            size={18}
            color="#BFD6FF"
          />

          <Text style={styles.itemTitle}>
            Joined
          </Text>

          <Text style={styles.itemValue}>
            {joinedDate}
          </Text>
        </View>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    borderRadius: 28,
    padding: 22,
    marginBottom: 24,
    elevation: 6,
    shadowColor: "#0B6E8E",
    shadowOpacity: 0.25,
    shadowRadius: 18,
    shadowOffset: {
      width: 0,
      height: 10,
    },
  },

  topRow: {
    flexDirection: "row",
    alignItems: "center",
  },

  avatar: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: "#FFFFFF",
    justifyContent: "center",
    alignItems: "center",
  },

  info: {
    flex: 1,
    marginLeft: 16,
  },

  name: {
    color: "#FFFFFF",
    fontSize: 22,
    fontWeight: "800",
  },

  id: {
    color: "rgba(255,255,255,0.8)",
    marginTop: 4,
    fontSize: 14,
    fontWeight: "600",
  },

  statusBadge: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    marginTop: 12,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },

  statusText: {
    color: "#FFF",
    marginLeft: 6,
    fontWeight: "700",
    fontSize: 12,
    letterSpacing: 0.5,
  },

  progressCircle: {
    width: 82,
    height: 82,
    borderRadius: 41,
    backgroundColor: "rgba(255,255,255,0.15)",
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.3)",
    justifyContent: "center",
    alignItems: "center",
  },

  progressValue: {
    color: "#FFF",
    fontSize: 22,
    fontWeight: "800",
  },

  progressLabel: {
    color: "#DCE6E9",
    fontSize: 11,
    marginTop: 2,
  },

  divider: {
    height: 1,
    backgroundColor: "rgba(255,255,255,0.18)",
    marginVertical: 22,
  },

  bottomRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },

  item: {
    flex: 1,
    alignItems: "center",
  },

  itemTitle: {
    color: "#BFD6FF",
    fontSize: 12,
    marginTop: 6,
  },

  itemValue: {
    color: "#FFF",
    fontSize: 13,
    fontWeight: "700",
    marginTop: 4,
    textAlign: "center",
  },
});