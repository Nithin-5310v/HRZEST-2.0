import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

export default function StatChip({
  label,
  color = "#0B6E8E",
  background = "#E3F2F5",
}) {
  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor: background,
        },
      ]}
    >
      <View
        style={[
          styles.dot,
          {
            backgroundColor: color,
          },
        ]}
      />

      <Text
        style={[
          styles.text,
          {
            color,
          },
        ]}
      >
        {label}
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

    paddingVertical: 7,

    borderRadius: 30,

    marginRight: 10,

    marginBottom: 10,
  },

  dot: {
    width: 8,

    height: 8,

    borderRadius: 4,

    marginRight: 8,
  },

  text: {
    fontSize: 13,

    fontWeight: "700",
  },
});