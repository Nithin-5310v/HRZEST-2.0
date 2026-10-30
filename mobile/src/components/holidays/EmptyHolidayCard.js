import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function EmptyHolidayCard({
  title = "No Holidays Found",
  subtitle = "There are no holidays available for the selected month.",
}) {
  return (
    <View style={styles.container}>
      <View style={styles.iconContainer}>
        <Ionicons
          name="calendar-clear-outline"
          size={58}
          color="#0B5CAD"
        />
      </View>

      <Text style={styles.title}>
        {title}
      </Text>

      <Text style={styles.subtitle}>
        {subtitle}
      </Text>

      <View style={styles.tipCard}>
        <Ionicons
          name="information-circle-outline"
          size={18}
          color="#0B5CAD"
        />

        <Text style={styles.tipText}>
          Holidays will automatically appear here once
          they are available for the selected year.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 28,

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    paddingVertical: 36,
    paddingHorizontal: 22,

    alignItems: "center",

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

  iconContainer: {
    width: 92,
    height: 92,

    borderRadius: 46,

    backgroundColor: "#E6EFFA",

    justifyContent: "center",
    alignItems: "center",
  },

  title: {
    marginTop: 22,

    fontSize: 22,

    fontWeight: "800",

    color: "#131C2A",
  },

  subtitle: {
    marginTop: 10,

    textAlign: "center",

    color: "#5A6678",

    fontSize: 15,

    lineHeight: 23,

    paddingHorizontal: 10,
  },

  tipCard: {
    marginTop: 28,

    width: "100%",

    backgroundColor: "#E6EFFA",

    borderRadius: 16,

    padding: 16,

    flexDirection: "row",

    alignItems: "flex-start",
  },

  tipText: {
    flex: 1,

    marginLeft: 10,

    color: "#455163",

    fontSize: 14,

    lineHeight: 22,

    fontWeight: "500",
  },
});