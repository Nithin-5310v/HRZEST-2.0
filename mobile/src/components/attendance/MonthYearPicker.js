import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export default function MonthYearPicker({
  month,
  year,
  onPrevious,
  onNext,
}) {
  return (
    <View style={styles.container}>
      {/* Header */}

      <View style={styles.header}>
        <View>
          <Text style={styles.smallTitle}>
            Attendance Overview
          </Text>

          <Text style={styles.title}>
            {MONTHS[month - 1]} {year}
          </Text>
        </View>

        <View style={styles.navigation}>
          <TouchableOpacity
            activeOpacity={0.8}
            style={styles.arrowButton}
            onPress={onPrevious}
          >
            <Ionicons
              name="chevron-back"
              size={20}
              color="#0B6E8E"
            />
          </TouchableOpacity>

          <TouchableOpacity
            activeOpacity={0.8}
            style={styles.arrowButton}
            onPress={onNext}
          >
            <Ionicons
              name="chevron-forward"
              size={20}
              color="#0B6E8E"
            />
          </TouchableOpacity>
        </View>
      </View>

      {/* Month Badge */}

      <View style={styles.badge}>
        <Ionicons
          name="calendar-outline"
          size={18}
          color="#0B6E8E"
        />

        <Text style={styles.badgeText}>
          {MONTHS[month - 1]} {year}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 18,
  },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },

  smallTitle: {
    fontSize: 13,
    color: "#5A6A70",
    fontWeight: "600",
  },

  title: {
    marginTop: 3,
    fontSize: 28,
    fontWeight: "800",
    color: "#132026",
  },

  navigation: {
    flexDirection: "row",
  },

  arrowButton: {
    width: 42,
    height: 42,
    borderRadius: 12,

    backgroundColor: "#FFFFFF",

    justifyContent: "center",
    alignItems: "center",

    marginLeft: 10,

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 8,
    shadowOffset: {
      width: 0,
      height: 3,
    },

    elevation: 3,
  },

  badge: {
    marginTop: 18,

    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",

    backgroundColor: "#E3F2F5",

    paddingHorizontal: 14,
    paddingVertical: 8,

    borderRadius: 24,
  },

  badgeText: {
    marginLeft: 8,
    color: "#0B6E8E",
    fontSize: 14,
    fontWeight: "700",
  },
});