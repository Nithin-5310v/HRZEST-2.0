import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function EmployeeAttendanceCard({

  attendance,

  checking,

  onCheckIn,

}) {

  const loginTime = attendance?.login_time || attendance?.check_in || (attendance?.status === "Checked In" ? "Present" : null);
  const logoutTime = attendance?.logout_time || attendance?.check_out;

  const checkedIn = Boolean((loginTime || attendance?.status === "Checked In") && !logoutTime);
  const completed = Boolean(loginTime && logoutTime);

  const status =
    attendance?.attendance_type ||
    attendance?.login_status ||
    attendance?.status ||
    (checkedIn ? "Checked In" : "Not Checked In");

  return (
    <View style={styles.card}>
      {/* Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Today's Attendance</Text>
          <Text style={styles.subtitle}>Live attendance information</Text>
        </View>

        <View style={styles.liveBadge}>
          <View style={styles.liveDot} />
          <Text style={styles.liveText}>LIVE</Text>
        </View>
      </View>

      {/* Time */}
      <View style={styles.timeContainer}>
        <View style={styles.timeBox}>
          <Ionicons name="log-in-outline" size={18} color="#16A34A" />
          <Text style={styles.timeLabel}>Check In</Text>
          <Text style={styles.timeValue}>
            {loginTime ? (loginTime.length > 5 ? loginTime.slice(0, 5) : loginTime) : "--:--"}
          </Text>
        </View>

        <View style={styles.divider} />

        <View style={styles.timeBox}>
          <Ionicons name="log-out-outline" size={18} color="#DC2626" />
          <Text style={styles.timeLabel}>Check Out</Text>
          <Text style={styles.timeValue}>
            {logoutTime ? (logoutTime.length > 5 ? logoutTime.slice(0, 5) : logoutTime) : "--:--"}
          </Text>
        </View>
      </View>

      {/* Status */}

      <View style={styles.statusContainer}>

        <View>

          <Text style={styles.statusLabel}>
            Current Status
          </Text>

          <Text style={styles.status}>
            {status}
          </Text>

        </View>

        <View
          style={[
            styles.statusDot,
            {
              backgroundColor:
                checkedIn
                  ? "#22C55E"
                  : completed
                  ? "#0B5CAD"
                  : "#C3CFDD",
            },
          ]}
        />

      </View>

      {/* Button */}
      <TouchableOpacity
        activeOpacity={0.9}
        disabled={checking}
        onPress={onCheckIn}
        style={[
          styles.button,
          checkedIn
            ? styles.checkoutButton
            : completed
            ? styles.reloginButton
            : styles.checkinButton,
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
        />
        <Text style={styles.buttonText}>
          {checking
            ? "Processing..."
            : checkedIn
            ? "Check Out"
            : completed
            ? "Re-Login / Punch In"
            : "Check In"}
        </Text>
      </TouchableOpacity>
    </View>

  );

}
const styles = StyleSheet.create({

  card: {

    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 20,

    marginBottom: 22,

    borderWidth: 1,

    borderColor: "#E9EEF5",

    shadowColor: "#131C2A",

    shadowOpacity: 0.05,

    shadowRadius: 18,

    shadowOffset: {
      width: 0,
      height: 8,
    },

    elevation: 5,

  },

  header: {

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    marginBottom: 22,

  },

  title: {

    fontSize: 18,

    fontWeight: "800",

    color: "#131C2A",

  },

  subtitle: {

    marginTop: 4,

    fontSize: 13,

    color: "#5A6678",

  },

  liveBadge: {

    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#ECFDF5",

    paddingHorizontal: 12,

    paddingVertical: 6,

    borderRadius: 20,

  },

  liveDot: {

    width: 8,

    height: 8,

    borderRadius: 4,

    backgroundColor: "#22C55E",

    marginRight: 6,

  },

  liveText: {

    color: "#16A34A",

    fontWeight: "700",

    fontSize: 11,

  },

  timeContainer: {

    flexDirection: "row",

    alignItems: "center",

    justifyContent: "space-between",

    backgroundColor: "#F5F8FC",

    borderRadius: 18,

    paddingVertical: 18,

    marginBottom: 22,

  },

  divider: {

    width: 1,

    height: 55,

    backgroundColor: "#E5E7EB",

  },

  timeBox: {

    flex: 1,

    alignItems: "center",

  },

  timeLabel: {

    marginTop: 8,

    color: "#5A6678",

    fontSize: 12,

  },

  timeValue: {

    marginTop: 5,

    fontSize: 22,

    fontWeight: "800",

    color: "#131C2A",

  },

  statusContainer: {

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    marginBottom: 22,

  },

  statusLabel: {

    fontSize: 12,

    color: "#8794A5",

    marginBottom: 4,

  },

  status: {

    fontSize: 18,

    fontWeight: "700",

    color: "#131C2A",

  },

  statusDot: {

    width: 14,

    height: 14,

    borderRadius: 7,

  },

  button: {

    height: 54,

    borderRadius: 16,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

  },

  checkinButton: {

    backgroundColor: "#0B5CAD",

  },

  checkoutButton: {

    backgroundColor: "#EF4444",

  },

  buttonText: {

    marginLeft: 10,

    color: "#FFFFFF",

    fontWeight: "700",

    fontSize: 15,

  },

  reloginButton: {
    backgroundColor: "#2E7BD1",
  },

  completedBox: {

    backgroundColor: "#ECFDF5",

    borderRadius: 16,

    paddingVertical: 15,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

  },

  completedText: {

    marginLeft: 8,

    color: "#15803D",

    fontWeight: "700",

    fontSize: 14,

  },

});