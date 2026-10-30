import React from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function ProfileMenuCard({
  icon = "person-outline",
  iconColor = "#0B5CAD",
  iconBackground = "#E6EFFA",
  title = "Personal Information",
  subtitle = "Manage your personal details",
  badge = "",
  onPress,
}) {
  return (
    <TouchableOpacity
      activeOpacity={0.85}
      style={styles.card}
      onPress={onPress}
    >
      {/* Left */}

      <View style={styles.left}>
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: iconBackground,
            },
          ]}
        >
          <Ionicons
            name={icon}
            size={22}
            color={iconColor}
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

      {/* Right */}

      <View style={styles.right}>
        {badge ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>
              {badge}
            </Text>
          </View>
        ) : null}

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

    borderRadius: 18,

    paddingHorizontal: 18,
    paddingVertical: 16,

    marginBottom: 14,

    borderWidth: 1,
    borderColor: "#E3EAF3",

    flexDirection: "row",

    alignItems: "center",

    justifyContent: "space-between",

    shadowColor: "#131C2A",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 4,
    },

    elevation: 2,
  },

  left: {
    flexDirection: "row",
    alignItems: "center",
    flex: 1,
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
    marginLeft: 16,
  },

  title: {
    fontSize: 16,
    fontWeight: "700",
    color: "#131C2A",
  },

  subtitle: {
    marginTop: 4,
    fontSize: 13,
    color: "#5A6678",
    lineHeight: 18,
  },

  right: {
    flexDirection: "row",
    alignItems: "center",
  },

  badge: {
    backgroundColor: "#ECFDF5",

    paddingHorizontal: 10,
    paddingVertical: 5,

    borderRadius: 20,

    marginRight: 10,
  },

  badgeText: {
    color: "#16A34A",
    fontSize: 11,
    fontWeight: "700",
  },
});