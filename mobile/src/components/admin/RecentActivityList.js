import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";
import THEME from "../../constants/theme";

const DEFAULT_ACTIVITIES = [
  {
    id: 1,
    title: "New Employee Joined",
    description: "Rahul Sharma joined Engineering.",
    time: "5 min ago",
    icon: "person-add",
    color: "#0B6E8E",
    bg: "#CDEBF2",
  },
  {
    id: 2,
    title: "Attendance Updated",
    description: "Today's attendance synchronized.",
    time: "20 min ago",
    icon: "calendar",
    color: "#16A34A",
    bg: "#DCFCE7",
  },
  {
    id: 3,
    title: "Payroll Generated",
    description: "June salary processed.",
    time: "1 hr ago",
    icon: "wallet",
    color: "#7C3AED",
    bg: "#EDE9FE",
  },
  {
    id: 4,
    title: "Leave Approved",
    description: "Priya's leave request approved.",
    time: "2 hrs ago",
    icon: "document-text",
    color: "#F59E0B",
    bg: "#FEF3C7",
  },
];

export default function RecentActivityList({
  activities = [],
  onViewAll = () => {},
}) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.heading}>
          Recent Activity
        </Text>

        <TouchableOpacity
          activeOpacity={0.8}
          onPress={onViewAll}
        >
          <Text style={styles.viewAll}>
            View All
          </Text>
        </TouchableOpacity>
      </View>

      {activities.length === 0 ? (
        <View style={{ backgroundColor: "#FFFFFF", borderRadius: 16, padding: 24, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: "#DCE6E9" }}>
          <Ionicons name="time-outline" size={36} color="#8A9AA0" />
          <Text style={{ fontSize: 14, fontWeight: "600", color: "#5A6A70", marginTop: 8 }}>
            No Recent System Activity
          </Text>
        </View>
      ) : (
        activities.map((item) => (
        <View
          key={item.id}
          style={styles.card}
        >
          <View
            style={[
              styles.iconContainer,
              {
                backgroundColor: item.bg,
              },
            ]}
          >
            <Ionicons
              name={item.icon}
              size={22}
              color={item.color}
            />
          </View>

          <View style={styles.info}>
            <Text
              numberOfLines={1}
              style={styles.title}
            >
              {item.title}
            </Text>

            <Text
              numberOfLines={2}
              style={styles.description}
            >
              {item.description}
            </Text>
          </View>

          <Text style={styles.time}>
            {item.time}
          </Text>
        </View>
      )))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 28,
  },

  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 18,
  },

  heading: {
    fontSize: 22,
    fontWeight: "800",
    color: "#132026",
  },

  viewAll: {
    fontSize: 14,
    color: THEME.colors.primary,
    fontWeight: "700",
  },

  card: {
    flexDirection: "row",
    alignItems: "center",

    backgroundColor: "#FFFFFF",

    borderRadius: 20,

    padding: 16,

    marginBottom: 14,

    borderWidth: 1,

    borderColor: "#E3ECEF",

    shadowColor: "#000",

    shadowOpacity: 0.04,

    shadowRadius: 10,

    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  iconContainer: {
    width: 52,

    height: 52,

    borderRadius: 16,

    justifyContent: "center",

    alignItems: "center",
  },

  info: {
    flex: 1,

    marginLeft: 14,
  },

  title: {
    fontSize: 15,

    fontWeight: "700",

    color: "#132026",
  },

  description: {
    marginTop: 4,

    fontSize: 13,

    color: "#5A6A70",

    lineHeight: 18,
  },

  time: {
    marginLeft: 10,

    fontSize: 12,

    color: "#8A9AA0",

    fontWeight: "600",
  },
});