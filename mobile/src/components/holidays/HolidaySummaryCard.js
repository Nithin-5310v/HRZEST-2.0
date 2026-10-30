import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

// holidays has no type column on the backend (see HolidayHeaderCard's
// note) -- holidayType default changed from a fabricated "Public Holiday"
// to a generic, always-true label.
export default function HolidaySummaryCard({
  upcomingHoliday = "No upcoming holiday",
  holidayDate = "",
  remainingDays = 0,
  holidayType = "Holiday",
  hasUpcoming = true,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="sparkles-outline"
            size={22}
            color="#0B5CAD"
          />
        </View>

        <View style={styles.headerContent}>
          <Text style={styles.title}>
            Upcoming Holiday
          </Text>

          <Text style={styles.subtitle}>
            Next official holiday
          </Text>
        </View>
      </View>

      <View style={styles.divider} />

      <Text style={styles.holidayName}>
        {upcomingHoliday}
      </Text>

      <View style={styles.infoRow}>
        <Ionicons
          name="calendar-outline"
          size={18}
          color="#5A6678"
        />

        <Text style={styles.infoText}>
          {holidayDate}
        </Text>
      </View>

      <View style={styles.infoRow}>
        <Ionicons
          name="flag-outline"
          size={18}
          color="#5A6678"
        />

        <Text style={styles.infoText}>
          {holidayType}
        </Text>
      </View>

      <View style={styles.daysCard}>
        <Ionicons
          name="time-outline"
          size={26}
          color="#FFFFFF"
        />

        <View style={styles.daysContent}>
          <Text style={styles.daysNumber}>
            {hasUpcoming ? remainingDays : "--"}
          </Text>

          <Text style={styles.daysLabel}>
            {hasUpcoming ? "Days Remaining" : "No holiday scheduled"}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 22,

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 20,

    borderWidth: 1,
    borderColor: "#E3EAF3",

    shadowColor: "#131C2A",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  header: {
    flexDirection: "row",
    alignItems: "center",
  },

  iconContainer: {
    width: 50,
    height: 50,

    borderRadius: 15,

    backgroundColor: "#E6EFFA",

    justifyContent: "center",
    alignItems: "center",
  },

  headerContent: {
    marginLeft: 14,
    flex: 1,
  },

  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#131C2A",
  },

  subtitle: {
    marginTop: 2,

    fontSize: 13,

    color: "#5A6678",

    fontWeight: "500",
  },

  divider: {
    height: 1,

    backgroundColor: "#E9EFF6",

    marginVertical: 18,
  },

  holidayName: {
    fontSize: 22,

    fontWeight: "800",

    color: "#0B5CAD",
  },

  infoRow: {
    flexDirection: "row",
    alignItems: "center",

    marginTop: 14,
  },

  infoText: {
    marginLeft: 10,

    fontSize: 15,

    color: "#455163",

    fontWeight: "600",
  },

  daysCard: {
    marginTop: 22,

    backgroundColor: "#0B5CAD",

    borderRadius: 18,

    padding: 16,

    flexDirection: "row",

    alignItems: "center",
  },

  daysContent: {
    marginLeft: 14,
  },

  daysNumber: {
    fontSize: 30,

    fontWeight: "800",

    color: "#FFFFFF",
  },

  daysLabel: {
    marginTop: 2,

    color: "rgba(255,255,255,0.85)",

    fontSize: 13,

    fontWeight: "600",
  },
});