import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function SummaryCard({
  icon,
  title,
  value,
  color = "#0B6E8E",
  background = "#E3F2F5",
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
          size={24}
          color={color}
        />
      </View>

      <View style={styles.content}>
        <Text style={styles.title}>
          {title}
        </Text>

        <Text style={styles.value}>
          ₹{Number(value).toLocaleString()}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",

    backgroundColor: "#FFFFFF",

    borderRadius: 20,

    padding: 18,

    marginBottom: 14,

    borderWidth: 1,
    borderColor: "#E3ECEF",

    shadowColor: "#132026",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,
  },

  iconContainer: {
    width: 56,
    height: 56,

    borderRadius: 16,

    justifyContent: "center",
    alignItems: "center",
  },

  content: {
    flex: 1,
    marginLeft: 16,
  },

  title: {
    fontSize: 14,
    fontWeight: "600",
    color: "#5A6A70",
  },

  value: {
    marginTop: 6,

    fontSize: 24,

    fontWeight: "800",

    color: "#132026",
  },
});