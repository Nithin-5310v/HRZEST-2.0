import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";
import THEME from "../../constants/theme";

export default function PendingApprovalCard({
  title = "Leave Requests",
  pending = 8,
  subtitle = "Requires your approval",
  icon = "document-text-outline",
  color = "#F59E0B",
  background = "#FEF3C7",
  onPress = () => {},
}) {
  return (
    <TouchableOpacity
      activeOpacity={0.9}
      style={styles.card}
      onPress={onPress}
    >
      <View style={styles.leftSection}>
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
            size={26}
            color={color}
          />
        </View>

        <View style={styles.info}>
          <Text style={styles.title}>
            {title}
          </Text>

          <Text style={styles.subtitle}>
            {subtitle}
          </Text>
        </View>
      </View>

      <View style={styles.rightSection}>
        <View style={styles.badge}>
          <Text style={styles.badgeText}>
            {pending}
          </Text>
        </View>

        <Ionicons
          name="chevron-forward"
          size={20}
          color="#8794A5"
        />
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FFFFFF",

    borderRadius: 24,

    padding: 18,

    marginBottom: 18,

    flexDirection: "row",

    justifyContent: "space-between",

    alignItems: "center",

    borderWidth: 1,

    borderColor: "#E3EAF3",

    shadowColor: "#000",

    shadowOpacity: 0.05,

    shadowRadius: 12,

    shadowOffset: {
      width: 0,
      height: 6,
    },

    elevation: 5,
  },

  leftSection: {
    flexDirection: "row",

    alignItems: "center",

    flex: 1,
  },

  iconContainer: {
    width: 58,

    height: 58,

    borderRadius: 18,

    justifyContent: "center",

    alignItems: "center",
  },

  info: {
    marginLeft: 16,

    flex: 1,
  },

  title: {
    fontSize: 17,

    fontWeight: "700",

    color: "#131C2A",
  },

  subtitle: {
    marginTop: 5,

    color: "#5A6678",

    fontSize: 13,

    fontWeight: "500",
  },

  rightSection: {
    alignItems: "center",
  },

  badge: {
    minWidth: 34,

    height: 34,

    borderRadius: 17,

    backgroundColor: THEME.colors.primary,

    justifyContent: "center",

    alignItems: "center",

    marginBottom: 6,
  },

  badgeText: {
    color: "#FFFFFF",

    fontWeight: "700",

    fontSize: 14,
  },
});