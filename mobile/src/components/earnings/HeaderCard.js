import React from "react";
import { View, Text, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

export default function HeaderCard({
  month,
  year,
  total,
  grossPay,
  incentives,
  overtime,
}) {
  return (
    <LinearGradient
      colors={["#0C3054", "#0B6E8E"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.container}
    >
      <Text style={styles.month}>
        {month} {year}
      </Text>

      <Text style={styles.title}>
        Estimated Earnings
      </Text>

      <Text style={styles.total}>
        ₹{total.toLocaleString()}
      </Text>

      <View style={styles.statsRow}>
        <View style={styles.card}>
          <Ionicons
            name="wallet-outline"
            size={20}
            color="#FFFFFF"
          />

          <Text style={styles.label}>
            Gross Pay
          </Text>

          <Text style={styles.value}>
            ₹{grossPay.toLocaleString()}
          </Text>
        </View>

        <View style={styles.card}>
          <Ionicons
            name="trophy-outline"
            size={20}
            color="#FFFFFF"
          />

          <Text style={styles.label}>
            Incentives
          </Text>

          <Text style={styles.value}>
            ₹{incentives.toLocaleString()}
          </Text>
        </View>

        <View style={styles.card}>
          <Ionicons
            name="time-outline"
            size={20}
            color="#FFFFFF"
          />

          <Text style={styles.label}>
            Overtime
          </Text>

          <Text style={styles.value}>
            ₹{overtime.toLocaleString()}
          </Text>
        </View>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    borderRadius: 24,
    padding: 22,
    marginBottom: 22,
  },

  month: {
    color: "rgba(255,255,255,0.8)",
    fontSize: 14,
    fontWeight: "600",
  },

  title: {
    color: "#FFFFFF",
    marginTop: 4,
    fontSize: 18,
    fontWeight: "700",
  },

  total: {
    color: "#FFFFFF",
    fontSize: 38,
    fontWeight: "900",
    marginTop: 10,
    marginBottom: 24,
  },

  statsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },

  card: {
    flex: 1,
    backgroundColor: "rgba(255,255,255,0.15)",
    borderRadius: 18,
    paddingVertical: 16,
    paddingHorizontal: 10,
    alignItems: "center",
    marginHorizontal: 4,
  },

  label: {
    color: "#FFFFFF",
    marginTop: 8,
    fontSize: 12,
    opacity: 0.9,
  },

  value: {
    color: "#FFFFFF",
    fontWeight: "800",
    marginTop: 6,
    fontSize: 16,
  },
});