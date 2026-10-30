import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function PolicyBullet({
  text,
  type = "normal",
}) {
  const getTheme = () => {
    switch (type) {
      case "success":
        return {
          icon: "checkmark-circle",
          color: "#16A34A",
          background: "#ECFDF5",
        };

      case "warning":
        return {
          icon: "warning",
          color: "#EA580C",
          background: "#FFF7ED",
        };

      case "danger":
        return {
          icon: "close-circle",
          color: "#DC2626",
          background: "#FEF2F2",
        };

      case "info":
        return {
          icon: "information-circle",
          color: "#0B5CAD",
          background: "#E6EFFA",
        };

      default:
        return {
          icon: "ellipse",
          color: "#0B5CAD",
          background: "#E6EFFA",
        };
    }
  };

  const theme = getTheme();

  return (
    <View style={styles.container}>
      <View
        style={[
          styles.iconContainer,
          {
            backgroundColor: theme.background,
          },
        ]}
      >
        <Ionicons
          name={theme.icon}
          size={14}
          color={theme.color}
        />
      </View>

      <Text style={styles.text}>
        {text}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",

    alignItems: "flex-start",

    marginBottom: 14,
  },

  iconContainer: {
    width: 28,
    height: 28,

    borderRadius: 14,

    justifyContent: "center",
    alignItems: "center",

    marginTop: 2,

    marginRight: 12,
  },

  text: {
    flex: 1,

    fontSize: 15,

    lineHeight: 24,

    color: "#455163",

    fontWeight: "500",
  },
});