import React from "react";
import { View, Text, StyleSheet, TouchableOpacity } from "react-native";
import { Ionicons } from "@expo/vector-icons";

const ACTIONS = [
  {
    title: "Attendance",
    subtitle: "View History",
    icon: "calendar-outline",
    color: "#0B6E8E",
    bg: "#E3F2F5",
    screen: "Attendance",
  },
  {
    title: "Earnings",
    subtitle: "Salary & Pay",
    icon: "wallet-outline",
    color: "#7C3AED",
    bg: "#F3E8FF",
    screen: "Earnings",
  },
  {
    title: "Leave",
    subtitle: "Apply / History",
    icon: "document-text-outline",
    color: "#16A34A",
    bg: "#ECFDF5",
    screen: "Leave",
  },
  {
    title: "Holidays",
    subtitle: "Calendar",
    icon: "gift-outline",
    color: "#DC2626",
    bg: "#FEF2F2",
    screen: "Holidays",
  },
  {
    title: "Support",
    subtitle: "Help Desk",
    icon: "headset-outline",
    color: "#EA580C",
    bg: "#FFF7ED",
    screen: "Tickets",
  },
  {
    title: "Profile",
    subtitle: "My Info",
    icon: "person-circle-outline",
    color: "#0284C7",
    bg: "#F0F9FF",
    screen: "Profile",
  },
];

export default function EmployeeQuickActions({ navigation }) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Quick Actions</Text>
        <Text style={styles.subtitle}>Frequently used features</Text>
      </View>
      <View style={styles.grid}>
        {ACTIONS.map(item => (
          <TouchableOpacity
            key={item.title}
            activeOpacity={0.88}
            style={styles.card}
            onPress={() => {
              if (navigation && item.screen) {
                navigation.navigate(item.screen);
              }
            }}
          >
            <View style={[styles.iconBox, { backgroundColor: item.bg }]}>
              <Ionicons name={item.icon} size={24} color={item.color} />
            </View>
            <Text style={styles.cardTitle}>{item.title}</Text>
            <Text style={styles.cardSubtitle}>{item.subtitle}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container:   { marginBottom: 24 },
  header:      { marginBottom: 16 },
  title:       { fontSize: 18, fontWeight: "700", color: "#132026" },
  subtitle:    { marginTop: 3, fontSize: 13, color: "#5A6A70" },
  grid:        { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between" },
  card: {
    width: "48%", backgroundColor: "#FFFFFF", borderRadius: 18,
    paddingVertical: 18, paddingHorizontal: 16, marginBottom: 14,
    borderWidth: 1, borderColor: "#E3ECEF",
    shadowColor: "#132026", shadowOpacity: 0.05, shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 }, elevation: 3,
  },
  iconBox:     { width: 48, height: 48, borderRadius: 14, justifyContent: "center", alignItems: "center", marginBottom: 14 },
  cardTitle:   { fontSize: 15, fontWeight: "700", color: "#132026" },
  cardSubtitle:{ marginTop: 4, fontSize: 12, color: "#5A6A70", lineHeight: 18 },
});
