import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function CompOffInfoCard() {
  return (
    <View style={styles.card}>
      <View style={styles.iconContainer}>
        <Ionicons
          name="information-circle"
          size={24}
          color="#0B6E8E"
        />
      </View>

      <View style={styles.content}>
        <Text style={styles.title}>
          Comp-off Policy
        </Text>

        <Text style={styles.description}>
          Employees who work more than{" "}
          <Text style={styles.highlight}>
            120 minutes
          </Text>{" "}
          of approved overtime in a day become eligible for
          comp-off credit.
        </Text>

        <View style={styles.divider} />

        <View style={styles.rule}>
          <Ionicons
            name="checkmark-circle"
            size={16}
            color="#16A34A"
          />

          <Text style={styles.ruleText}>
            Every{" "}
            <Text style={styles.highlight}>
              480 OT minutes
            </Text>{" "}
            (8 hours) earns{" "}
            <Text style={styles.highlight}>
              1 Comp-off Day
            </Text>.
          </Text>
        </View>

        <View style={styles.rule}>
          <Ionicons
            name="time-outline"
            size={16}
            color="#F59E0B"
          />

          <Text style={styles.ruleText}>
            Overtime must be approved by your reporting
            manager before credit is added.
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 20,

    marginBottom: 22,

    flexDirection: "row",
    alignItems: "flex-start",

    borderWidth: 1,
    borderColor: "#E9EEF5",

    shadowColor: "#132026",
    shadowOpacity: 0.05,
    shadowRadius: 14,
    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 4,
  },

  iconContainer: {
    width: 52,
    height: 52,

    borderRadius: 16,

    backgroundColor: "#E3F2F5",

    justifyContent: "center",
    alignItems: "center",
  },

  content: {
    flex: 1,
    marginLeft: 16,
  },

  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#132026",
  },

  description: {
    marginTop: 8,

    color: "#5A6A70",

    fontSize: 14,

    lineHeight: 22,
  },

  highlight: {
    color: "#0B6E8E",
    fontWeight: "800",
  },

  divider: {
    height: 1,

    backgroundColor: "#E9F1F3",

    marginVertical: 16,
  },

  rule: {
    flexDirection: "row",
    alignItems: "flex-start",

    marginBottom: 12,
  },

  ruleText: {
    flex: 1,

    marginLeft: 10,

    color: "#44555B",

    fontSize: 13,

    lineHeight: 20,

    fontWeight: "500",
  },
});