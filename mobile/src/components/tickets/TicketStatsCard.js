import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

function StatCard({
  icon,
  title,
  value,
  color,
  background,
}) {
  return (
    <View style={styles.statCard}>
      <View
        style={[
          styles.iconContainer,
          {
            backgroundColor: background,
          },
        ]}
      >
        <Ionicons
          name={icon}
          size={22}
          color={color}
        />
      </View>

      <Text style={styles.value}>
        {value}
      </Text>

      <Text style={styles.label}>
        {title}
      </Text>
    </View>
  );
}

export default function TicketStatsCard({
  open = 3,
  inProgress = 2,
  resolved = 8,
  closed = 12,
}) {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Ionicons
          name="stats-chart-outline"
          size={22}
          color="#0B5CAD"
        />

        <Text style={styles.title}>
          Ticket Overview
        </Text>
      </View>

      <View style={styles.grid}>
        <StatCard
          icon="alert-circle"
          title="Open"
          value={open}
          color="#EA580C"
          background="#FFF7ED"
        />

        <StatCard
          icon="sync"
          title="In Progress"
          value={inProgress}
          color="#0B5CAD"
          background="#E6EFFA"
        />

        <StatCard
          icon="checkmark-circle"
          title="Resolved"
          value={resolved}
          color="#22C55E"
          background="#ECFDF5"
        />

        <StatCard
          icon="archive"
          title="Closed"
          value={closed}
          color="#5A6678"
          background="#EDF2F8"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 20,

    marginBottom: 24,

    borderWidth: 1,
    borderColor: "#E3EAF3",

    shadowColor: "#131C2A",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  header: {
    flexDirection: "row",

    alignItems: "center",

    marginBottom: 20,
  },

  title: {
    marginLeft: 10,

    fontSize: 20,

    fontWeight: "800",

    color: "#131C2A",
  },

  grid: {
    flexDirection: "row",

    flexWrap: "wrap",

    justifyContent: "space-between",
  },

  statCard: {
    width: "48%",

    backgroundColor: "#F5F8FC",

    borderRadius: 18,

    paddingVertical: 20,

    alignItems: "center",

    marginBottom: 14,

    borderWidth: 1,
    borderColor: "#E9EFF6",
  },

  iconContainer: {
    width: 52,
    height: 52,

    borderRadius: 26,

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 12,
  },

  value: {
    fontSize: 26,

    fontWeight: "800",

    color: "#131C2A",
  },

  label: {
    marginTop: 6,

    fontSize: 13,

    fontWeight: "700",

    color: "#5A6678",

    textAlign: "center",
  },
});