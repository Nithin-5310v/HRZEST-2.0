import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function AttendanceStatusCard({
  checkIn = "--:--",
  checkOut = "--:--",
  workingHours = "--",
  status = "Not Marked",
  onCheckIn,
  checking = false,
}) {
  const checkedIn = checkIn !== "--:--" && checkOut === "--:--";
  const completed = checkIn !== "--:--" && checkOut !== "--:--";

  const statusColor =
    status === "Present" || checkedIn
      ? "#22C55E"
      : status === "Late"
      ? "#F59E0B"
      : status === "Absent"
      ? "#EF4444"
      : "#8A9AA0";

  const statusBg =
    status === "Present" || checkedIn
      ? "#ECFDF5"
      : status === "Late"
      ? "#FFFBEB"
      : status === "Absent"
      ? "#FEF2F2"
      : "#ECF3F5";

  return (
    <View style={styles.card}>
      {/* Header */}

      <View style={styles.header}>
        <View>
          <Text style={styles.title}>
            Today's Attendance
          </Text>

          <Text style={styles.subtitle}>
            Live attendance information
          </Text>
        </View>

        <View style={styles.liveBadge}>
          <View style={styles.liveDot} />

          <Text style={styles.liveText}>
            LIVE
          </Text>
        </View>
      </View>

      {/* Check In / Out */}

      <View style={styles.timeContainer}>
        <View style={styles.timeBox}>
          <View
            style={[
              styles.iconCircle,
              { backgroundColor: "#ECFDF5" },
            ]}
          >
            <Ionicons
              name="log-in-outline"
              size={22}
              color="#22C55E"
            />
          </View>

          <Text style={styles.timeLabel}>
            Check In
          </Text>

          <Text style={styles.timeValue}>
            {checkIn}
          </Text>
        </View>

        <View style={styles.divider} />

        <View style={styles.timeBox}>
          <View
            style={[
              styles.iconCircle,
              { backgroundColor: "#FEF2F2" },
            ]}
          >
            <Ionicons
              name="log-out-outline"
              size={22}
              color="#EF4444"
            />
          </View>

          <Text style={styles.timeLabel}>
            Check Out
          </Text>

          <Text style={styles.timeValue}>
            {checkOut}
          </Text>
        </View>
      </View>

      {/* Working Hours */}

      <View style={styles.workCard}>
        <Ionicons
          name="time-outline"
          size={18}
          color="#0B6E8E"
        />

        <View style={{ marginLeft: 10 }}>
          <Text style={styles.workLabel}>
            Working Hours
          </Text>

          <Text style={styles.workHours}>
            {workingHours}
          </Text>
        </View>
      </View>

      {/* Action Button */}
      {onCheckIn && (
        <TouchableOpacity
          activeOpacity={0.88}
          disabled={checking}
          onPress={onCheckIn}
          style={[
            styles.actionBtn,
            checkedIn
              ? styles.checkoutBtn
              : completed
              ? styles.reloginBtn
              : styles.checkinBtn,
          ]}
        >
          <Ionicons
            name={
              checkedIn
                ? "log-out-outline"
                : completed
                ? "refresh-outline"
                : "log-in-outline"
            }
            size={20}
            color="#FFFFFF"
            style={{ marginRight: 8 }}
          />
          <Text style={styles.actionBtnText}>
            {checking
              ? "Processing..."
              : checkedIn
              ? "Check Out Shift"
              : completed
              ? "Re-Login / Start Session"
              : "Mark Attendance / Check In"}
          </Text>
        </TouchableOpacity>
      )}

      {/* Status */}

      <View style={styles.statusRow}>
        <Text style={styles.statusTitle}>
          Current Status
        </Text>

        <View
          style={[
            styles.statusBadge,
            { backgroundColor: statusBg },
          ]}
        >
          <View
            style={[
              styles.statusDot,
              { backgroundColor: statusColor },
            ]}
          />

          <Text
            style={[
              styles.statusText,
              { color: statusColor },
            ]}
          >
            {checkedIn ? "Working" : status}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",
    borderRadius: 24,
    padding: 20,

    marginBottom: 18,

    shadowColor: "#000",
    shadowOpacity: 0.08,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 5,
  },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",

    marginBottom: 18,
  },

  title: {
    fontSize: 15,
    fontWeight: "800",
    color: "#132026",
  },

  subtitle: {
    marginTop: 4,
    fontSize: 12,
    color: "#5A6A70",
  },

  liveBadge: {
    flexDirection: "row",
    alignItems: "center",

    backgroundColor: "#DCFCE7",

    paddingHorizontal: 10,
    paddingVertical: 5,

    borderRadius: 20,
  },

  liveDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: "#22C55E",

    marginRight: 6,
  },

  liveText: {
    color: "#15803D",
    fontWeight: "700",
    fontSize: 11,
  },

  timeContainer: {
    flexDirection: "row",

    backgroundColor: "#F5FAFB",

    borderRadius: 18,

    overflow: "hidden",

    borderWidth: 1,
    borderColor: "#E9F1F3",
  },

  divider: {
    width: 1,
    backgroundColor: "#E5E7EB",
  },

  timeBox: {
    flex: 1,

    alignItems: "center",

    paddingVertical: 18,
  },

  iconCircle: {
    width: 42,
    height: 42,
    borderRadius: 21,

    justifyContent: "center",
    alignItems: "center",
  },

  timeLabel: {
    marginTop: 10,
    color: "#5A6A70",
    fontSize: 11,
  },

  timeValue: {
    marginTop: 4,
    fontSize: 18,
    fontWeight: "800",
    color: "#132026",
  },

  workCard: {
    marginTop: 18,

    backgroundColor: "#E3F2F5",

    borderRadius: 16,

    padding: 14,

    flexDirection: "row",
    alignItems: "center",
  },

  workLabel: {
    color: "#5A6A70",
    fontSize: 11,
  },

  workHours: {
    marginTop: 2,
    fontSize: 16,
    fontWeight: "800",
    color: "#0B6E8E",
  },

  statusRow: {
    marginTop: 18,

    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },

  statusTitle: {
    fontWeight: "700",
    fontSize: 13,
    color: "#132026",
  },

  statusBadge: {
    flexDirection: "row",
    alignItems: "center",

    paddingHorizontal: 12,
    paddingVertical: 7,

    borderRadius: 20,
  },

  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,

    marginRight: 7,
  },

  statusText: {
    fontWeight: "700",
    fontSize: 13,
  },

  actionBtn: {
    marginTop: 16,
    height: 48,
    borderRadius: 16,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16,
  },

  checkinBtn: {
    backgroundColor: "#0B6E8E",
  },

  checkoutBtn: {
    backgroundColor: "#DC2626",
  },

  reloginBtn: {
    backgroundColor: "#1B8AA6",
  },

  actionBtnText: {
    color: "#FFFFFF",
    fontSize: 15,
    fontWeight: "800",
  },

  completedBadge: {
    marginTop: 16,
    height: 44,
    borderRadius: 16,
    backgroundColor: "#DCFCE7",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16,
  },

  completedText: {
    color: "#15803D",
    fontSize: 14,
    fontWeight: "800",
  },
});