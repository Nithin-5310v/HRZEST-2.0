import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

const ACTIONS = [
  {
    title: "Staff Directory",
    icon: "people",
    color: "#0C3054",
    background: "#E8F5F7",
    screen: "Employees",
  },
  {
    title: "Attendance Logs",
    icon: "calendar",
    color: "#16A34A",
    background: "#DCFCE7",
    screen: "Attendance",
  },
  {
    title: "Payroll & Payslips",
    icon: "wallet",
    color: "#7C3AED",
    background: "#EDE9FE",
    screen: "Payroll",
  },
  {
    title: "Leave Approvals",
    icon: "document-text",
    color: "#F59E0B",
    background: "#FEF3C7",
    screen: "LeaveRequests",
  },
];

export default function QuickActionGrid({ navigation }) {
  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Quick Actions</Text>

      <View style={styles.grid}>
        {ACTIONS.map((item) => (
          <TouchableOpacity
            key={item.title}
            activeOpacity={0.85}
            style={styles.card}
            onPress={() => {
              if (navigation) {
                navigation.navigate(item.screen);
              }
            }}
          >
            <View
              style={[
                styles.iconContainer,
                { backgroundColor: item.background },
              ]}
            >
              <Ionicons name={item.icon} size={28} color={item.color} />
            </View>

            <Text numberOfLines={2} style={styles.title}>
              {item.title}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 26,
  },
  heading: {
    fontSize: 20,
    fontWeight: "800",
    color: "#132026",
    marginBottom: 16,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
  },
  card: {
    width: "48.5%",
    backgroundColor: "#FFFFFF",
    borderRadius: 20,
    paddingVertical: 18,
    alignItems: "center",
    marginBottom: 14,
    borderWidth: 1,
    borderColor: "#DCE6E9",
    shadowColor: "#132026",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  iconContainer: {
    width: 56,
    height: 56,
    borderRadius: 18,
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 12,
  },
  title: {
    fontSize: 14,
    fontWeight: "700",
    color: "#132026",
    textAlign: "center",
    paddingHorizontal: 6,
  },
});