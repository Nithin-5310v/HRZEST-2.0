import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import TicketStatusBadge from "./TicketStatusBadge";

export default function TicketCard({
  ticketId,
  category,
  subject,
  priority,
  createdAt,
  status,
  onPress = () => {},
}) {
  const priorityColor = () => {
    switch (priority) {
      case "Critical":
        return "#DC2626";

      case "High":
        return "#EA580C";

      case "Medium":
        return "#F59E0B";

      default:
        return "#22C55E";
    }
  };

  return (
    <TouchableOpacity
      activeOpacity={0.9}
      style={styles.container}
      onPress={onPress}
    >
      {/* Header */}

      <View style={styles.header}>
        <View>
          <Text style={styles.ticketId}>
            #{ticketId}
          </Text>

          <Text style={styles.date}>
            {createdAt}
          </Text>
        </View>

        <TicketStatusBadge
          status={status}
        />
      </View>

      {/* Subject */}

      <Text style={styles.subject}>
        {subject}
      </Text>

      {/* Category */}

      <View style={styles.infoRow}>
        <Ionicons
          name="folder-open-outline"
          size={18}
          color="#2563EB"
        />

        <Text style={styles.infoText}>
          {category}
        </Text>
      </View>

      {/* Priority */}

      <View style={styles.infoRow}>
        <Ionicons
          name="flag-outline"
          size={18}
          color={priorityColor()}
        />

        <Text
          style={[
            styles.priority,
            {
              color: priorityColor(),
            },
          ]}
        >
          {priority} Priority
        </Text>
      </View>

      {/* Footer */}

      <View style={styles.footer}>
        <View style={styles.footerLeft}>
          <Ionicons
            name="time-outline"
            size={16}
            color="#667085"
          />

          <Text style={styles.footerText}>
            View Details
          </Text>
        </View>

        <Ionicons
          name="chevron-forward"
          size={20}
          color="#667085"
        />
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 18,

    marginBottom: 18,

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
  },

  header: {
    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    marginBottom: 14,
  },

  ticketId: {
    fontSize: 17,

    fontWeight: "800",

    color: "#2563EB",
  },

  date: {
    marginTop: 4,

    fontSize: 13,

    color: "#667085",
  },

  subject: {
    fontSize: 18,

    fontWeight: "800",

    color: "#101828",

    marginBottom: 18,

    lineHeight: 26,
  },

  infoRow: {
    flexDirection: "row",

    alignItems: "center",

    marginBottom: 10,
  },

  infoText: {
    marginLeft: 10,

    fontSize: 15,

    fontWeight: "600",

    color: "#475467",
  },

  priority: {
    marginLeft: 10,

    fontSize: 15,

    fontWeight: "700",
  },

  footer: {
    marginTop: 16,

    paddingTop: 16,

    borderTopWidth: 1,

    borderTopColor: "#EEF1F5",

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",
  },

  footerLeft: {
    flexDirection: "row",
    alignItems: "center",
  },

  footerText: {
    marginLeft: 6,

    color: "#475467",

    fontWeight: "600",

    fontSize: 14,
  },
});