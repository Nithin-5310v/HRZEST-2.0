import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function TicketStatusBadge({
  status = "Open",
}) {
  const getStatus = () => {
    switch (status) {
      case "Open":
        return {
          color: "#EA580C",
          background: "#FFF7ED",
          icon: "alert-circle",
        };

      case "In Progress":
        return {
          color: "#0B6E8E",
          background: "#E3F2F5",
          icon: "sync",
        };

      case "Resolved":
        return {
          color: "#22C55E",
          background: "#ECFDF5",
          icon: "checkmark-circle",
        };

      case "Closed":
        return {
          color: "#5A6A70",
          background: "#ECF3F5",
          icon: "archive",
        };

      case "Rejected":
        return {
          color: "#DC2626",
          background: "#FEF2F2",
          icon: "close-circle",
        };

      default:
        return {
          color: "#5A6A70",
          background: "#F5FAFB",
          icon: "ellipse",
        };
    }
  };

  const badge = getStatus();

  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor:
            badge.background,
        },
      ]}
    >
      <Ionicons
        name={badge.icon}
        size={14}
        color={badge.color}
      />

      <Text
        style={[
          styles.text,
          {
            color: badge.color,
          },
        ]}
      >
        {status}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",

    alignItems: "center",

    alignSelf: "flex-start",

    paddingHorizontal: 12,

    paddingVertical: 6,

    borderRadius: 20,
  },

  text: {
    marginLeft: 6,

    fontSize: 12,

    fontWeight: "800",

    letterSpacing: 0.3,
  },
});