import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

const categories = [
  {
    id: "hr",
    title: "HR",
    icon: "people-outline",
    color: "#0B6E8E",
    background: "#E3F2F5",
  },
  {
    id: "it",
    title: "IT",
    icon: "desktop-outline",
    color: "#7C3AED",
    background: "#F5F3FF",
  },
  {
    id: "admin",
    title: "Admin",
    icon: "business-outline",
    color: "#EA580C",
    background: "#FFF7ED",
  },
  {
    id: "payroll",
    title: "Payroll",
    icon: "wallet-outline",
    color: "#16A34A",
    background: "#ECFDF5",
  },
];

export default function TicketCategoryPicker({
  selectedCategory,
  onSelectCategory,
}) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>
        Category
      </Text>

      <View style={styles.grid}>
        {categories.map((item) => {
          const active =
            selectedCategory === item.id;

          return (
            <TouchableOpacity
              key={item.id}
              activeOpacity={0.9}
              style={[
                styles.card,
                {
                  backgroundColor: active
                    ? item.background
                    : "#FFFFFF",
                  borderColor: active
                    ? item.color
                    : "#E5E7EB",
                },
              ]}
              onPress={() =>
                onSelectCategory(item.id)
              }
            >
              <View
                style={[
                  styles.iconContainer,
                  {
                    backgroundColor:
                      item.background,
                  },
                ]}
              >
                <Ionicons
                  name={item.icon}
                  size={22}
                  color={item.color}
                />
              </View>

              <Text
                style={[
                  styles.title,
                  active && {
                    color: item.color,
                  },
                ]}
              >
                {item.title}
              </Text>

              {active && (
                <Ionicons
                  name="checkmark-circle"
                  size={18}
                  color={item.color}
                  style={styles.check}
                />
              )}
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
    color: "#132026",
    marginBottom: 14,
  },

  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
  },

  card: {
    width: "48%",

    borderWidth: 1.5,

    borderRadius: 18,

    paddingVertical: 18,

    paddingHorizontal: 14,

    marginBottom: 14,

    alignItems: "center",

    backgroundColor: "#FFFFFF",

    shadowColor: "#000",
    shadowOpacity: 0.03,
    shadowRadius: 6,
    shadowOffset: {
      width: 0,
      height: 3,
    },

    elevation: 2,
  },

  iconContainer: {
    width: 52,
    height: 52,

    borderRadius: 26,

    justifyContent: "center",
    alignItems: "center",

    marginBottom: 12,
  },

  title: {
    fontSize: 15,
    fontWeight: "700",
    color: "#2E3D43",
  },

  check: {
    position: "absolute",
    top: 10,
    right: 10,
  },
});