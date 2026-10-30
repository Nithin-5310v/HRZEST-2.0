import React from "react";
import { View, Text, StyleSheet } from "react-native";

const LEGENDS = [
  {
    label: "Present",
    color: "#22C55E",
  },
  {
    label: "Absent",
    color: "#EF4444",
  },
  {
    label: "Late",
    color: "#F59E0B",
  },
  {
    label: "Half Day",
    color: "#FB923C",
  },
  {
    label: "Holiday",
    color: "#8B5CF6",
  },
];

export default function AttendanceLegend() {
  return (
    <View style={styles.card}>
      <Text style={styles.title}>
        Attendance Legend
      </Text>

      <View style={styles.container}>
        {LEGENDS.map((item) => (
          <View
            key={item.label}
            style={styles.item}
          >
            <View
              style={[
                styles.dot,
                {
                  backgroundColor: item.color,
                },
              ]}
            />

            <Text style={styles.label}>
              {item.label}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginTop: 18,

    backgroundColor: "#FFFFFF",

    borderRadius: 20,

    paddingHorizontal: 18,
    paddingVertical: 18,

    borderWidth: 1,
    borderColor: "#E9F1F3",

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },
    elevation: 3,
  },

  title: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0B6E8E",

    marginBottom: 14,
  },

  container: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
  },

  item: {
    width: "48%",

    flexDirection: "row",
    alignItems: "center",

    marginBottom: 14,
  },

  dot: {
    width: 14,
    height: 14,
    borderRadius: 7,

    marginRight: 10,
  },

  label: {
    fontSize: 14,
    fontWeight: "600",
    color: "#2E3D43",
  },
});