import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Image,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../../store/AuthContext";

export default function EmployeeHeroCard({
  employeeName,
  designation,
  employeeId,
  date,
  attendance,
  onCheckIn,
  checking,
  onMenu,
  onLogout,
  photoUrl,
  onScanQR,
  onOpenIdCard,
  onOpenAiHelpdesk,
}) {
  const { user } = useAuth();

  const loginTime = attendance?.login_time || attendance?.check_in || (attendance?.status === "Checked In" ? "Present" : null);
  const logoutTime = attendance?.logout_time || attendance?.check_out;
  const checkedIn = Boolean((loginTime || attendance?.status === "Checked In") && !logoutTime);
  const completed = Boolean(loginTime && logoutTime);

  const greeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return "Good Morning";
    if (hour < 17) return "Good Afternoon";
    return "Good Evening";
  };

  const displayName = employeeName || user?.name || "Staff Member";
  const displayRole = designation || user?.role || "Verified Employee";
  const displayEmpId = employeeId || user?.employeeId || "EMP-1001";

  const todayStr = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <LinearGradient
      colors={["#0C3054", "#0B5CAD"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.card}
    >
      {/* User Welcome Row */}
      <View style={styles.userRow}>
        <View style={styles.avatarWrapper}>
          {photoUrl ? (
            <Image source={{ uri: photoUrl }} style={styles.avatarImg} />
          ) : (
            <View style={styles.initialBadge}>
              <Text style={styles.initialText}>
                {displayName.charAt(0).toUpperCase()}
              </Text>
            </View>
          )}
          <View style={styles.onlineDot} />
        </View>

        <View style={styles.userInfo}>
          <Text style={styles.greetingText}>{greeting()}, 👋</Text>
          <Text style={styles.nameText} numberOfLines={1}>
            {displayName}
          </Text>

          <View style={styles.metaRow}>
            <View style={styles.roleTag}>
              <Ionicons name="shield-checkmark" size={12} color="#4C9BE8" style={{ marginRight: 4 }} />
              <Text style={styles.roleText} numberOfLines={1}>
                {displayRole}
              </Text>
            </View>
            <View style={styles.idTag}>
              <Text style={styles.idText}>ID: {displayEmpId}</Text>
            </View>
          </View>
        </View>
      </View>

      {/* Live Status Bar */}
      <View style={styles.statusBar}>
        <TouchableOpacity
          activeOpacity={0.8}
          disabled={checking || completed}
          onPress={onCheckIn}
          style={styles.statusLeft}
        >
          <View
            style={[
              styles.statusDot,
              { backgroundColor: completed ? "#8794A5" : checkedIn ? "#4ADE80" : "#FBBF24" },
            ]}
          />
          <Text style={styles.statusTitle}>
            {checking
              ? "Processing..."
              : completed
              ? "Shift Completed"
              : checkedIn
              ? "Currently Checked In"
              : "Tap to Check In"}
          </Text>
        </TouchableOpacity>
        <Text style={styles.dateText}>{todayStr}</Text>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 24,
    padding: 20,
    marginBottom: 18,
    elevation: 6,
    shadowColor: "#0C3054",
    shadowOpacity: 0.35,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
  },
  userRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 18,
  },
  avatarWrapper: {
    position: "relative",
    marginRight: 14,
  },
  avatarImg: {
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: 2,
    borderColor: "#FFFFFF",
  },
  initialBadge: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: "#FFFFFF",
    justifyContent: "center",
    alignItems: "center",
  },
  initialText: {
    fontSize: 22,
    fontWeight: "900",
    color: "#0B5CAD",
  },
  onlineDot: {
    position: "absolute",
    right: 0,
    bottom: 2,
    width: 12,
    height: 12,
    borderRadius: 6,
    backgroundColor: "#4ADE80",
    borderWidth: 2,
    borderColor: "#0C3054",
  },
  userInfo: {
    flex: 1,
  },
  greetingText: {
    fontSize: 13,
    color: "rgba(255, 255, 255, 0.8)",
    fontWeight: "600",
  },
  nameText: {
    fontSize: 20,
    fontWeight: "800",
    color: "#FFFFFF",
    marginVertical: 2,
  },
  metaRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 4,
    gap: 8,
    flexWrap: "wrap",
  },
  roleTag: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(255, 255, 255, 0.12)",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  roleText: {
    color: "#DBE3EE",
    fontSize: 11,
    fontWeight: "700",
  },
  idTag: {
    backgroundColor: "rgba(56, 189, 248, 0.2)",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  idText: {
    color: "#4C9BE8",
    fontSize: 11,
    fontWeight: "800",
  },
  statusBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "rgba(0, 0, 0, 0.2)",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 14,
  },
  statusLeft: {
    flexDirection: "row",
    alignItems: "center",
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 8,
  },
  statusTitle: {
    color: "#FFFFFF",
    fontSize: 12,
    fontWeight: "700",
  },
  dateText: {
    color: "rgba(255, 255, 255, 0.75)",
    fontSize: 11,
    fontWeight: "600",
  },
});