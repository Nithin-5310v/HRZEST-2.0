import React, { useState, useEffect } from "react";
import { View, Text, StyleSheet, TouchableOpacity } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

export default function AttendanceOverviewCard({
  present = 0,
  absent = 0,
  late = 0,
  onLeave = 0,
  navigation,
}) {
  const [timeStr, setTimeStr] = useState("");
  const [dateStr, setDateStr] = useState("");
  const [countdown, setCountdown] = useState(30);

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(
        now.toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: true,
        })
      );
      setDateStr(
        now
          .toLocaleDateString("en-US", {
            weekday: "long",
            day: "numeric",
            month: "short",
            year: "numeric",
          })
          .toUpperCase()
      );
    };

    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown((prev) => (prev <= 1 ? 30 : prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const total = present + absent + late + onLeave;
  const percentage = total > 0 ? Math.round((present / total) * 100) : 0;

  const data = [
    {
      label: "Present",
      value: present,
      icon: "checkmark-circle",
      color: "#22C55E",
      bg: "#ECFDF5",
    },
    {
      label: "Absent",
      value: absent,
      icon: "close-circle",
      color: "#EF4444",
      bg: "#FEF2F2",
    },
    {
      label: "Late",
      value: late,
      icon: "time",
      color: "#F59E0B",
      bg: "#FFFBEB",
    },
    {
      label: "On Leave",
      value: onLeave,
      icon: "airplane",
      color: "#0C3054",
      bg: "#E8F5F7",
    },
  ];

  return (
    <TouchableOpacity
      activeOpacity={0.92}
      style={styles.card}
      onPress={() => {
        if (navigation) navigation.navigate("Attendance");
      }}
    >
      {/* Real-time Clock Banner */}
      <LinearGradient
        colors={["#0C3054", "#0B6E8E"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.clockBanner}
      >
        <View style={styles.clockTopRow}>
          <View style={styles.clockBadge}>
            <View style={styles.livePulseDot} />
            <Text style={styles.clockBadgeText}>REAL-TIME ATTENDANCE CLOCK</Text>
          </View>
          <View style={styles.syncRing}>
            <Ionicons name="refresh-circle-outline" size={14} color="rgba(255,255,255,0.85)" />
            <Text style={styles.syncText}>Sync {countdown}s</Text>
          </View>
        </View>

        <Text style={styles.liveTimeText}>{timeStr || "12:00:00 AM"}</Text>
        <Text style={styles.liveDateText}>{dateStr}</Text>
      </LinearGradient>

      {/* Overview Stats Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Today's Attendance</Text>
          <Text style={styles.subtitle}>Live workforce status overview</Text>
        </View>

        <View style={styles.scoreBox}>
          <Text style={styles.score}>{percentage}%</Text>
          <Text style={styles.scoreLabel}>Rate</Text>
        </View>
      </View>

      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${percentage}%` }]} />
      </View>

      <View style={styles.list}>
        {data.map((item) => (
          <View key={item.label} style={styles.row}>
            <View style={styles.left}>
              <View style={[styles.iconBox, { backgroundColor: item.bg }]}>
                <Ionicons name={item.icon} size={18} color={item.color} />
              </View>
              <Text style={styles.label}>{item.label}</Text>
            </View>
            <Text style={styles.value}>{item.value}</Text>
          </View>
        ))}
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",
    borderRadius: 22,
    padding: 18,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: "#DCE6E9",
    shadowColor: "#132026",
    shadowOpacity: 0.04,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },

  // Clock Banner
  clockBanner: {
    borderRadius: 16,
    padding: 16,
    marginBottom: 18,
    alignItems: "center",
    shadowColor: "#0C3054",
    shadowOpacity: 0.2,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  clockTopRow: {
    width: "100%",
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 10,
  },
  clockBadge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.15)",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
  },
  livePulseDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: "#22C55E",
    marginRight: 6,
  },
  clockBadgeText: {
    fontSize: 10,
    fontWeight: "800",
    color: "#FFFFFF",
    letterSpacing: 0.6,
  },
  syncRing: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.15)",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
  },
  syncText: {
    fontSize: 10,
    fontWeight: "700",
    color: "rgba(255,255,255,0.9)",
    marginLeft: 4,
  },
  liveTimeText: {
    fontSize: 28,
    fontWeight: "800",
    color: "#FFFFFF",
    letterSpacing: 1.5,
  },
  liveDateText: {
    fontSize: 11,
    fontWeight: "600",
    color: "rgba(255,255,255,0.8)",
    marginTop: 4,
    letterSpacing: 0.5,
  },

  // Overview Stats Header
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 14,
  },
  title: {
    fontSize: 17,
    fontWeight: "800",
    color: "#132026",
  },
  subtitle: {
    marginTop: 2,
    fontSize: 12,
    color: "#5A6A70",
  },
  scoreBox: {
    alignItems: "flex-end",
  },
  score: {
    fontSize: 22,
    fontWeight: "800",
    color: "#0C3054",
    letterSpacing: -0.4,
  },
  scoreLabel: {
    fontSize: 10,
    fontWeight: "700",
    color: "#5A6A70",
    marginTop: 1,
  },
  progressTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: "#ECF3F5",
    overflow: "hidden",
    marginBottom: 16,
  },
  progressFill: {
    height: "100%",
    backgroundColor: "#0C3054",
    borderRadius: 4,
  },
  list: {
    gap: 10,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  left: {
    flexDirection: "row",
    alignItems: "center",
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: 10,
    justifyContent: "center",
    alignItems: "center",
    marginRight: 12,
  },
  label: {
    fontSize: 14,
    fontWeight: "600",
    color: "#2E3D43",
  },
  value: {
    fontSize: 16,
    fontWeight: "800",
    color: "#132026",
  },
});