import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const priorities = [
  {
    id: "Low",
    icon: "flag-outline",
    color: "#22C55E",
    background: "#ECFDF5",
  },
  {
    id: "Medium",
    icon: "flag-outline",
    color: "#F59E0B",
    background: "#FFF7ED",
  },
  {
    id: "High",
    icon: "warning-outline",
    color: "#EA580C",
    background: "#FFF7ED",
  },
  {
    id: "Critical",
    icon: "alert-circle-outline",
    color: "#DC2626",
    background: "#FEF2F2",
  },
];

export default function PrioritySelector({
  selectedPriority,
  onSelectPriority,
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>
        Priority
      </Text>

      <View style={styles.row}>
        {priorities.map((item) => {
          const active =
            selectedPriority === item.id;

          return (
            <TouchableOpacity
              key={item.id}
              activeOpacity={0.9}
              style={[
                styles.priorityButton,
                {
                  backgroundColor: active
                    ? item.background
                    : "#FFFFFF",

                  borderColor: active
                    ? item.color
                    : "#DBE3EE",
                },
              ]}
              onPress={() =>
                onSelectPriority(item.id)
              }
            >
              <Ionicons
                name={item.icon}
                size={18}
                color={
                  active
                    ? item.color
                    : "#8794A5"
                }
              />

              <Text
                style={[
                  styles.priorityText,
                  active && {
                    color: item.color,
                  },
                ]}
              >
                {item.id}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 22,
  },

  label: {
    fontSize: 16,
    fontWeight: "800",
    color: "#131C2A",
    marginBottom: 14,
  },

  row: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
  },

  priorityButton: {
    width: "48%",

    height: 54,

    borderRadius: 16,

    borderWidth: 1.5,

    flexDirection: "row",

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 12,

    backgroundColor: "#FFFFFF",

    shadowColor: "#000",
    shadowOpacity: 0.03,
    shadowRadius: 5,
    shadowOffset: {
      width: 0,
      height: 2,
    },

    elevation: 1,
  },

  priorityText: {
    marginLeft: 8,

    fontSize: 14,

    fontWeight: "700",

    color: "#455163",
  },
});