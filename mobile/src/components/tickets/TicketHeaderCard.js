import React from "react";
import {
  View,
  Text,
  StyleSheet,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

export default function TicketHeaderCard({
  totalTickets = 12,
  openTickets = 3,
  resolvedTickets = 9,
}) {
  return (
    <View style={styles.container}>
      {/* Left */}

      <View style={styles.left}>
        <View style={styles.iconContainer}>
          <Ionicons
            name="ticket"
            size={34}
            color="#0B6E8E"
          />
        </View>

        <View style={styles.textContainer}>
          <Text style={styles.title}>
            Support Center
          </Text>

          <Text style={styles.subtitle}>
            Raise HR, IT and Admin support
            requests quickly.
          </Text>
        </View>
      </View>

      {/* Divider */}

      <View style={styles.divider} />

      {/* Statistics */}

      <View style={styles.statsRow}>
        <View style={styles.statCard}>
          <Text style={styles.statNumber}>
            {totalTickets}
          </Text>

          <Text style={styles.statLabel}>
            Total
          </Text>
        </View>

        <View
          style={[
            styles.statCard,
            styles.middleCard,
          ]}
        >
          <Text
            style={[
              styles.statNumber,
              {
                color: "#EA580C",
              },
            ]}
          >
            {openTickets}
          </Text>

          <Text style={styles.statLabel}>
            Open
          </Text>
        </View>

        <View style={styles.statCard}>
          <Text
            style={[
              styles.statNumber,
              {
                color: "#16A34A",
              },
            ]}
          >
            {resolvedTickets}
          </Text>

          <Text style={styles.statLabel}>
            Resolved
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

    marginBottom: 22,

    borderWidth: 1,
    borderColor: "#E3ECEF",

    shadowColor: "#132026",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 3,
  },

  left: {
    flexDirection: "row",
    alignItems: "center",
  },

  iconContainer: {
    width: 70,
    height: 70,

    borderRadius: 35,

    backgroundColor: "#E3F2F5",

    justifyContent: "center",
    alignItems: "center",
  },

  textContainer: {
    flex: 1,
    marginLeft: 18,
  },

  title: {
    fontSize: 22,
    fontWeight: "800",
    color: "#132026",
  },

  subtitle: {
    marginTop: 8,

    fontSize: 14,

    color: "#5A6A70",

    lineHeight: 21,
  },

  divider: {
    height: 1,

    backgroundColor: "#E9F1F3",

    marginVertical: 22,
  },

  statsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },

  statCard: {
    flex: 1,

    backgroundColor: "#F5FAFB",

    borderRadius: 18,

    alignItems: "center",

    paddingVertical: 16,
  },

  middleCard: {
    marginHorizontal: 12,
  },

  statNumber: {
    fontSize: 24,

    fontWeight: "800",

    color: "#0B6E8E",
  },

  statLabel: {
    marginTop: 6,

    fontSize: 13,

    fontWeight: "700",

    color: "#5A6A70",
  },
});