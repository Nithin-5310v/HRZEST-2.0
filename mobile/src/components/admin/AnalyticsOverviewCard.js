import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function AnalyticsOverviewCard({
  navigation,
  productivity = "0%",
  performance = "N/A",
  attrition = "0%",
}) {
  const cards = [
    {
      title: "Productivity",
      value: productivity,
      icon: "trending-up",
      color: "#16A34A",
      background: "#DCFCE7",
      screen: "Analytics",
    },
    {
      title: "Performance",
      value: performance,
      icon: "ribbon",
      color: "#0C3054",
      background: "#EBF2FB",
      screen: "Performance",
    },
    {
      title: "Attrition",
      value: attrition,
      icon: "people",
      color: "#F59E0B",
      background: "#FEF3C7",
      screen: "Analytics",
    },
  ];
  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Analytics Overview</Text>

      <View style={styles.row}>
        {cards.map((item) => (
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
              <Ionicons name={item.icon} size={24} color={item.color} />
            </View>

            <Text style={styles.value}>{item.value}</Text>
            <Text style={styles.label}>{item.title}</Text>
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
    color: "#131C2A",
    marginBottom: 16,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  card: {
    width: "31.5%",
    backgroundColor: "#FFFFFF",
    borderRadius: 20,
    paddingVertical: 16,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#DBE3EE",
    shadowColor: "#131C2A",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  iconContainer: {
    width: 48,
    height: 48,
    borderRadius: 16,
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 10,
  },
  value: {
    fontSize: 22,
    fontWeight: "800",
    color: "#131C2A",
  },
  label: {
    marginTop: 4,
    fontSize: 12,
    color: "#5A6678",
    fontWeight: "600",
  },
});