import React from "react";
import {
  View,
  Text,
 StyleSheet,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

export default function HolidayCard({
  title = "Republic Day",
  date = "26 January 2026",
  day = "Monday",
  type = "Public",
  description = "National Holiday",
}) {
  const getTypeStyles = () => {
    switch (type) {
      case "Public":
        return {
          icon: "flag-outline",
          color: "#DC2626",
          background: "#FEF2F2",
        };

      case "Company":
        return {
          icon: "business-outline",
          color: "#0B6E8E",
          background: "#E3F2F5",
        };

      case "Optional":
        return {
          icon: "star-outline",
          color: "#7C3AED",
          background: "#F5F3FF",
        };

      default:
        return {
          icon: "calendar-outline",
          color: "#5A6A70",
          background: "#F5FAFB",
        };
    }
  };

  const style = getTypeStyles();

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: style.background,
            },
          ]}
        >
          <Ionicons
            name={style.icon}
            size={24}
            color={style.color}
          />
        </View>

        <View style={styles.titleContainer}>
          <Text style={styles.title}>
            {title}
          </Text>

          <Text style={styles.day}>
            {day}
          </Text>
        </View>

        <View
          style={[
            styles.badge,
            {
              backgroundColor: style.background,
            },
          ]}
        >
          <Text
            style={[
              styles.badgeText,
              {
                color: style.color,
              },
            ]}
          >
            {type}
          </Text>
        </View>
      </View>

      <View style={styles.divider} />

      <View style={styles.infoRow}>
        <Ionicons
          name="calendar-outline"
          size={18}
          color="#5A6A70"
        />

        <Text style={styles.infoText}>
          {date}
        </Text>
      </View>

      <View style={styles.infoRow}>
        <Ionicons
          name="information-circle-outline"
          size={18}
          color="#5A6A70"
        />

        <Text style={styles.infoText}>
          {description}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 16,

    backgroundColor: "#FFFFFF",

    borderRadius: 20,

    padding: 18,

    borderWidth: 1,

    borderColor: "#E3ECEF",

    shadowColor: "#000",
    shadowOpacity: 0.04,
    shadowRadius: 10,
    shadowOffset: {
      width: 0,
      height: 5,
    },

    elevation: 2,
  },

  header: {
    flexDirection: "row",
    alignItems: "center",
  },

  iconContainer: {
    width: 54,
    height: 54,

    borderRadius: 16,

    justifyContent: "center",
    alignItems: "center",
  },

  titleContainer: {
    flex: 1,
    marginLeft: 14,
  },

  title: {
    fontSize: 16,
    fontWeight: "800",
    color: "#132026",
  },

  day: {
    marginTop: 2,
    fontSize: 13,
    color: "#5A6A70",
    fontWeight: "600",
  },

  badge: {
    paddingHorizontal: 12,

    paddingVertical: 6,

    borderRadius: 20,
  },

  badgeText: {
    fontSize: 12,

    fontWeight: "800",
  },

  divider: {
    height: 1,

    backgroundColor: "#E9F1F3",

    marginVertical: 16,
  },

  infoRow: {
    flexDirection: "row",

    alignItems: "center",

    marginBottom: 10,
  },

  infoText: {
    flex: 1,

    marginLeft: 10,

    fontSize: 14,

    color: "#44555B",

    fontWeight: "500",
  },
});