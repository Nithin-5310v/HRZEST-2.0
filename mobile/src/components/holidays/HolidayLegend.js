import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

export default function HolidayLegend() {
  const items = [
    {
      title: "Public Holiday",
      color: "#EF4444",
      background: "#FEF2F2",
    },
    {
      title: "Company Holiday",
      color: "#0B6E8E",
      background: "#E3F2F5",
    },
    {
      title: "Optional Holiday",
      color: "#7C3AED",
      background: "#F5F3FF",
    },
    {
      title: "Today",
      color: "#22C55E",
      background: "#ECFDF5",
    },
    {
      title: "Working Day",
      color: "#8A9AA0",
      background: "#F5FAFB",
    },
  ];

  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        Calendar Legend
      </Text>

      <View style={styles.wrapper}>
        {items.map((item) => (
          <View
            key={item.title}
            style={[
              styles.legendItem,
              {
                backgroundColor: item.background,
              },
            ]}
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
              {item.title}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: 22,

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 18,

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

  title: {
    fontSize: 16,
    fontWeight: "800",
    color: "#132026",
    marginBottom: 14,
  },

  wrapper: {
    flexDirection: "row",

    flexWrap: "wrap",

    justifyContent: "space-between",
  },

  legendItem: {
    width: "48%",

    flexDirection: "row",

    alignItems: "center",

    paddingVertical: 12,

    paddingHorizontal: 14,

    borderRadius: 14,

    marginBottom: 12,
  },

  dot: {
    width: 14,

    height: 14,

    borderRadius: 7,

    marginRight: 10,
  },

  label: {
    flex: 1,

    fontSize: 14,

    fontWeight: "700",

    color: "#2E3D43",
  },
});