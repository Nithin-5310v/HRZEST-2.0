import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function EmptyTickets({
  onCreateTicket = () => {},
}) {
  return (
    <View style={styles.container}>
      <View style={styles.iconContainer}>
        <Ionicons
          name="ticket-outline"
          size={70}
          color="#2563EB"
        />
      </View>

      <Text style={styles.title}>
        No Tickets Yet
      </Text>

      <Text style={styles.subtitle}>
        You haven't raised any support tickets.
        Create a ticket whenever you need help
        from HR, IT, Payroll, or Administration.
      </Text>

      <TouchableOpacity
        activeOpacity={0.9}
        style={styles.button}
        onPress={onCreateTicket}
      >
        <Ionicons
          name="add-circle-outline"
          size={22}
          color="#FFFFFF"
        />

        <Text style={styles.buttonText}>
          Raise Your First Ticket
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    paddingVertical: 40,
    paddingHorizontal: 24,

    alignItems: "center",

    borderWidth: 1,
    borderColor: "#E6E9EF",

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,

    marginVertical: 20,
  },

  iconContainer: {
    width: 110,
    height: 110,

    borderRadius: 55,

    backgroundColor: "#EEF4FF",

    justifyContent: "center",
    alignItems: "center",

    marginBottom: 22,
  },

  title: {
    fontSize: 22,

    fontWeight: "800",

    color: "#101828",

    marginBottom: 10,
  },

  subtitle: {
    fontSize: 15,

    lineHeight: 24,

    color: "#475467",

    textAlign: "center",

    marginBottom: 28,
  },

  button: {
    flexDirection: "row",

    alignItems: "center",

    justifyContent: "center",

    backgroundColor: "#2563EB",

    borderRadius: 16,

    paddingHorizontal: 24,
    paddingVertical: 14,

    shadowColor: "#2563EB",
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 4,
  },

  buttonText: {
    marginLeft: 10,

    color: "#FFFFFF",

    fontSize: 16,

    fontWeight: "700",
  },
});