import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

// The holidays table (app.py's init_db) only has date + name -- no
// public/company/optional type classification exists anywhere on the
// backend (web's own admin holiday management has the same limitation),
// so this card shows the one real number available -- total holidays --
// rather than a fabricated 3-way breakdown that used to be hardcoded
// 12/4/2 regardless of what was actually in the database.
export default function HolidayHeaderCard({
  year = "2026",
  totalHolidays = 0,
  upcomingCount = 0,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.topRow}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="calendar-outline"
            size={30}
            color="#FFFFFF"
          />
        </View>

        <View style={styles.titleContainer}>
          <Text style={styles.title}>
            Holiday Calendar
          </Text>

          <Text style={styles.subtitle}>
            Public holidays & company leaves
          </Text>
        </View>
      </View>

      <View style={styles.yearCard}>
        <Text style={styles.yearLabel}>
          Calendar Year
        </Text>

        <Text style={styles.year}>
          {year}
        </Text>
      </View>

      <View style={styles.statsRow}>
        <View style={styles.statCard}>
          <Ionicons
            name="calendar-clear-outline"
            size={22}
            color="#0B6E8E"
          />

          <Text style={styles.statNumber}>
            {totalHolidays}
          </Text>

          <Text style={styles.statLabel}>
            Total Holidays
          </Text>
        </View>

        <View style={styles.statCard}>
          <Ionicons
            name="flag-outline"
            size={22}
            color="#22C55E"
          />

          <Text style={styles.statNumber}>
            {upcomingCount}
          </Text>

          <Text style={styles.statLabel}>
            Upcoming
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 12,

    backgroundColor: "#0B6E8E",

    borderRadius: 24,

    padding: 20,

    shadowColor: "#0B6E8E",
    shadowOpacity: 0.22,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 8,
    },

    elevation: 6,
  },

  topRow: {
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

  titleContainer: {
    marginLeft: 16,
    flex: 1,
  },

  title: {
    color: "#FFFFFF",
    fontSize: 24,
    fontWeight: "800",
  },

  subtitle: {
    marginTop: 4,

    color: "rgba(255,255,255,0.82)",

    fontSize: 14,

    fontWeight: "500",
  },

  yearCard: {
    marginTop: 22,

    backgroundColor: "rgba(255,255,255,0.12)",

    borderRadius: 18,

    paddingVertical: 14,

    alignItems: "center",
  },

  yearLabel: {
    color: "rgba(255,255,255,0.75)",

    fontSize: 13,

    fontWeight: "600",
  },

  year: {
    marginTop: 4,

    color: "#FFFFFF",

    fontSize: 22,

    fontWeight: "800",

    letterSpacing: -0.4,
  },

  statsRow: {
    flexDirection: "row",
    justifyContent: "space-between",

    marginTop: 22,
  },

  statCard: {
    width: "23%",

    backgroundColor: "#FFFFFF",

    borderRadius: 18,

    paddingVertical: 14,

    alignItems: "center",
  },

  statNumber: {
    marginTop: 8,

    fontSize: 20,

    fontWeight: "800",

    color: "#132026",
  },

  statLabel: {
    marginTop: 4,

    fontSize: 12,

    fontWeight: "600",

    color: "#5A6A70",

    textAlign: "center",
  },
});