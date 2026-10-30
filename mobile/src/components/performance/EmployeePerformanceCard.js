import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function EmployeePerformanceCard({
  name,
  designation,
  department,
  employeeId,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.avatar}>
        <Ionicons
          name="person"
          size={42}
          color="#0B6E8E"
        />
      </View>

      <View style={styles.info}>
        <Text style={styles.name}>
          {name}
        </Text>

        <Text style={styles.designation}>
          {designation}
        </Text>

        <Text style={styles.department}>
          {department}
        </Text>

        <View style={styles.badge}>
          <Ionicons
            name="card-outline"
            size={14}
            color="#0B6E8E"
          />

          <Text style={styles.badgeText}>
            {employeeId}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 22,

    flexDirection: "row",
    alignItems: "center",

    borderWidth: 1,
    borderColor: "#E3ECEF",

    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,

    marginBottom: 22,
  },

  avatar: {
    width: 82,
    height: 82,

    borderRadius: 41,

    backgroundColor: "#E3F2F5",

    justifyContent: "center",
    alignItems: "center",

    borderWidth: 2,
    borderColor: "#D8E7FF",
  },

  info: {
    flex: 1,
    marginLeft: 18,
  },

  name: {
    fontSize: 22,
    fontWeight: "800",
    color: "#132026",
  },

  designation: {
    marginTop: 6,
    fontSize: 16,
    fontWeight: "700",
    color: "#0B6E8E",
  },

  department: {
    marginTop: 4,
    fontSize: 14,
    color: "#5A6A70",
    fontWeight: "600",
  },

  badge: {
    marginTop: 14,

    alignSelf: "flex-start",

    flexDirection: "row",
    alignItems: "center",

    backgroundColor: "#E3F2F5",

    paddingHorizontal: 12,
    paddingVertical: 7,

    borderRadius: 30,
  },

  badgeText: {
    marginLeft: 6,

    color: "#0B6E8E",

    fontWeight: "700",

    fontSize: 13,
  },
});