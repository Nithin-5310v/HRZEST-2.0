import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function AttendanceSummaryCard({
  percentage = 0,
  present = 0,
  absent = 0,
  late = 0,
}) {
  return (
    <View style={styles.container}>
      {/* Attendance % */}

      <View style={styles.leftCard}>
        <View style={styles.circle}>
          <Text style={styles.percent}>
            {percentage}%
          </Text>

          <Text style={styles.percentLabel}>
            Attendance
          </Text>
        </View>
      </View>

      {/* Right Stats */}

      <View style={styles.rightCard}>
        <View style={styles.row}>
          <View style={[styles.iconBox, { backgroundColor: "#DCFCE7" }]}>
            <Ionicons
              name="checkmark-circle"
              size={18}
              color="#22C55E"
            />
          </View>

          <View style={styles.textContainer}>
            <Text style={styles.value}>
              {present}
            </Text>

            <Text style={styles.label}>
              Present
            </Text>
          </View>
        </View>

        <View style={styles.divider} />

        <View style={styles.row}>
          <View style={[styles.iconBox, { backgroundColor: "#FEF3C7" }]}>
            <Ionicons
              name="time"
              size={18}
              color="#F59E0B"
            />
          </View>

          <View style={styles.textContainer}>
            <Text style={styles.value}>
              {late}
            </Text>

            <Text style={styles.label}>
              Late
            </Text>
          </View>
        </View>

        <View style={styles.divider} />

        <View style={styles.row}>
          <View style={[styles.iconBox, { backgroundColor: "#FEE2E2" }]}>
            <Ionicons
              name="close-circle"
              size={18}
              color="#EF4444"
            />
          </View>

          <View style={styles.textContainer}>
            <Text style={styles.value}>
              {absent}
            </Text>

            <Text style={styles.label}>
              Absent
            </Text>
          </View>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    marginBottom: 22,
  },

  leftCard: {
    flex: 1,

    backgroundColor: "#0B5CAD",

    borderRadius: 24,

    justifyContent: "center",
    alignItems: "center",

    marginRight: 12,

    paddingVertical: 24,

    shadowColor: "#0B5CAD",
    shadowOpacity: 0.18,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 6,
  },

  circle: {
    width: 130,
    height: 130,
    borderRadius: 65,

    borderWidth: 8,
    borderColor: "rgba(255,255,255,0.18)",

    justifyContent: "center",
    alignItems: "center",
  },

  percent: {
    color: "#FFFFFF",
    fontSize: 32,
    fontWeight: "800",
  },

  percentLabel: {
    color: "rgba(255,255,255,.75)",
    marginTop: 4,
    fontWeight: "600",
  },

  rightCard: {
    flex: 1,

    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 18,

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 4,
  },

  row: {
    flexDirection: "row",
    alignItems: "center",
  },

  iconBox: {
    width: 42,
    height: 42,
    borderRadius: 12,

    justifyContent: "center",
    alignItems: "center",
  },

  textContainer: {
    marginLeft: 12,
  },

  value: {
    fontSize: 22,
    fontWeight: "800",
    color: "#131C2A",
  },

  label: {
    marginTop: 2,
    color: "#5A6678",
    fontWeight: "600",
  },

  divider: {
    height: 1,
    backgroundColor: "#E9EFF6",
    marginVertical: 16,
  },
});