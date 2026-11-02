import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function BreakdownRow({
  icon,
  label,
  value,
  color = "#2563EB",
  background = "#EEF4FF",
  valueColor = "#101828",
}) {
  return (
    <View style={styles.container}>
      <View
        style={[
          styles.iconContainer,
          { backgroundColor: background },
        ]}
      >
        <Ionicons
          name={icon}
          size={20}
          color={color}
        />
      </View>

      <Text style={styles.label}>
        {label}
      </Text>

      <Text
        style={[
          styles.value,
          { color: valueColor },
        ]}
      >
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",

    paddingVertical: 14,

    borderBottomWidth: 1,
    borderBottomColor: "#EEF1F5",
  },

  iconContainer: {
    width: 40,
    height: 40,

    borderRadius: 12,

    justifyContent: "center",
    alignItems: "center",
  },

  label: {
    flex: 1,

    marginLeft: 14,

    fontSize: 15,

    fontWeight: "600",

    color: "#344054",
  },

  value: {
    fontSize: 16,
    fontWeight: "800",
  },
});