import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function AttendanceEmptyState() {
  return (
    <View style={styles.container}>
      <Ionicons
        name="calendar-outline"
        size={70}
        color="#D0D5DD"
      />

      <Text style={styles.title}>
        No Attendance Found
      </Text>

      <Text style={styles.subtitle}>
        Attendance records for this month are not available.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 40,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 30,
  },

  title: {
    marginTop: 16,
    fontSize: 18,
    fontWeight: "700",
    color: "#2563EB",
  },

  subtitle: {
    marginTop: 8,
    textAlign: "center",
    color: "#475467",
    fontSize: 14,
    lineHeight: 22,
  },
});