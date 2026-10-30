import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function HighlightCard({
  type = "info",
  title,
  description,
}) {
  const getStyles = () => {
    switch (type) {
      case "success":
        return {
          background: "#ECFDF5",
          border: "#22C55E",
          icon: "checkmark-circle",
          color: "#15803D",
        };

      case "warning":
        return {
          background: "#FFF7ED",
          border: "#F59E0B",
          icon: "warning",
          color: "#B45309",
        };

      case "danger":
        return {
          background: "#FEF2F2",
          border: "#EF4444",
          icon: "alert-circle",
          color: "#DC2626",
        };

      default:
        return {
          background: "#E3F2F5",
          border: "#0B6E8E",
          icon: "information-circle",
          color: "#0B6E8E",
        };
    }
  };

  const theme = getStyles();

  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor: theme.background,
          borderLeftColor: theme.border,
        },
      ]}
    >
      <View style={styles.iconContainer}>
        <Ionicons
          name={theme.icon}
          size={24}
          color={theme.color}
        />
      </View>

      <View style={styles.content}>
        <Text
          style={[
            styles.title,
            {
              color: theme.color,
            },
          ]}
        >
          {title}
        </Text>

        <Text style={styles.description}>
          {description}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",

    borderLeftWidth: 5,

    borderRadius: 18,

    padding: 18,

    marginBottom: 22,
  },

  iconContainer: {
    width: 46,
    height: 46,

    borderRadius: 23,

    backgroundColor: "#FFFFFF",

    justifyContent: "center",
    alignItems: "center",

    marginRight: 16,
  },

  content: {
    flex: 1,
  },

  title: {
    fontSize: 17,

    fontWeight: "800",

    marginBottom: 8,
  },

  description: {
    fontSize: 14,

    lineHeight: 22,

    color: "#44555B",
  },
});