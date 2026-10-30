import React from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from "react-native";

import { Ionicons } from "@expo/vector-icons";

import THEME from "../../constants/theme";

// The company's latest announcements (blueprints/core.py's /api/dashboard),
// newest first; the card shows the top three and View All opens the rest.
const SHOWN = 3;

function getIcon(type) {
  switch (type) {
    case "holiday":
      return {
        icon: "airplane",
        color: "#0B6E8E",
        bg: "#CDEBF2",
      };

    case "meeting":
      return {
        icon: "people",
        color: "#16A34A",
        bg: "#DCFCE7",
      };

    case "payroll":
      return {
        icon: "wallet",
        color: "#7C3AED",
        bg: "#EDE9FE",
      };

    default:
      return {
        icon: "notifications",
        color: "#F59E0B",
        bg: "#FEF3C7",
      };
  }
}

export default function AnnouncementCard({

  announcements = [],

  onViewAll = () => {},

}) {

  return (

    <View style={styles.container}>

      <View style={styles.header}>

        <Text style={styles.heading}>
          Announcements
        </Text>

        <TouchableOpacity
          onPress={onViewAll}
          activeOpacity={0.8}
        >
          <Text style={styles.viewAll}>
            View All
          </Text>
        </TouchableOpacity>

      </View>

      {announcements.length === 0 ? (
        <View style={styles.card}>
          <Text style={styles.message}>
            No announcements yet. Post one from Notifications & Broadcast.
          </Text>
        </View>
      ) : null}

      {announcements.slice(0, SHOWN).map((item) => {

        const config = getIcon(item.type);

        return (

          <View
            key={item.id}
            style={styles.card}
          >

            <View
              style={[
                styles.iconContainer,
                {
                  backgroundColor:
                    config.bg,
                },
              ]}
            >

              <Ionicons
                name={config.icon}
                size={22}
                color={config.color}
              />

            </View>

            <View style={styles.content}>

              <Text style={styles.title}>
                {item.title}
              </Text>

              <Text
                numberOfLines={2}
                style={styles.message}
              >
                {item.message}
              </Text>

            </View>

            <Text style={styles.date}>
              {item.date}
            </Text>

          </View>

        );

      })}

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

    color: THEME.colors.primary,

    fontWeight: "700",

    fontSize: 14,

  },

  card: {

    flexDirection: "row",

    alignItems: "center",

    backgroundColor: "#FFFFFF",

    borderRadius: 22,

    padding: 16,

    marginBottom: 14,

    borderWidth: 1,

    borderColor: "#E3ECEF",

    shadowColor: "#000",

    shadowOpacity: 0.05,

    shadowRadius: 10,

    shadowOffset: {

      width: 0,

      height: 5,

    },

    elevation: 4,

  },

  iconContainer: {

    width: 54,

    height: 54,

    borderRadius: 18,

    justifyContent: "center",

    alignItems: "center",

  },

  content: {

    flex: 1,

    marginLeft: 16,

  },

  title: {

    fontSize: 15,

    fontWeight: "700",

    color: "#132026",

  },

  message: {

    marginTop: 5,

    fontSize: 13,

    color: "#5A6A70",

    lineHeight: 18,

  },

  date: {

    marginLeft: 10,

    fontSize: 12,

    color: "#8A9AA0",

    fontWeight: "600",

  },

});